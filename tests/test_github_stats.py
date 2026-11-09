import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import github_stats as gs  # noqa: E402
import today  # noqa: E402

USER_ID = "U_me"


def commit(oid, additions=10, deletions=2, parents=1):
    return {"oid": oid, "additions": additions, "deletions": deletions, "parents": {"totalCount": parents}}


def repo(repo_id, head, owner="martinczarkowski", private=False, stars=0):
    return {"id": repo_id, "nameWithOwner": f"{owner}/{repo_id}", "isPrivate": private, "stargazerCount": stars,
            "owner": {"login": owner}, "defaultBranchRef": {"target": {"oid": head}} if head else None}


class FakeClient:
    """Answers the module's queries from in-memory data; histories are newest first."""

    def __init__(self, affiliated, contributed=(), histories=None, failing=()):
        self.affiliated, self.contributed = list(affiliated), list(contributed)
        self.histories, self.failing = histories or {}, set(failing)
        self.history_calls = []

    def graphql(self, query, variables):
        if query is gs.USER_QUERY:
            return {"user": {"id": USER_ID, "createdAt": "2019-12-13T13:46:19Z", "followers": {"totalCount": 3}}}
        if query in (gs.AFFILIATED_QUERY, gs.CONTRIBUTED_QUERY):
            nodes = self.affiliated if query is gs.AFFILIATED_QUERY else self.contributed
            if variables["privacy"] == "PUBLIC":
                nodes = [node for node in nodes if not node["isPrivate"]]
            return {"user": {"connection": {"nodes": nodes, "pageInfo": {"hasNextPage": False, "endCursor": None}}}}
        repo_id = variables["id"]
        self.history_calls.append(repo_id)
        if repo_id in self.failing:
            raise gs.GitHubError("HTTP 502")
        nodes = self.histories[repo_id]
        start = int(variables["cursor"] or 0)
        page = nodes[start:start + variables["first"]]
        has_next = start + variables["first"] < len(nodes)
        history = {"totalCount": len(nodes), "nodes": page,
                   "pageInfo": {"hasNextPage": has_next, "endCursor": str(start + variables["first"])}}
        return {"node": {"defaultBranchRef": {"target": {"history": history}}}}


class CollectTests(unittest.TestCase):
    def test_collect_forkSharesCommits_countsEachCommitOnce(self):
        client = FakeClient([repo("a", "h1"), repo("fork", "h2")],
                            histories={"a": [commit("c1"), commit("c2")], "fork": [commit("c1"), commit("c2")]})
        values, errors = gs.collect(client, "martinczarkowski", False, {})
        self.assertEqual(errors, [])
        self.assertEqual((values["commits"], values["additions"], values["deletions"]), (2, 20, 4))

    def test_collect_mergeCommit_excludedFromCommitsAndLines(self):
        client = FakeClient([repo("a", "h1")], histories={"a": [commit("m", 500, 500, parents=2), commit("c1")]})
        values, _ = gs.collect(client, "martinczarkowski", False, {})
        self.assertEqual((values["commits"], values["additions"]), (1, 10))

    def test_collect_publicMode_ignoresPrivateRepositories(self):
        client = FakeClient([repo("pub", "h1", stars=2), repo("priv", "h2", private=True, stars=5)],
                            histories={"pub": [commit("c1")], "priv": [commit("c2")]})
        values, _ = gs.collect(client, "martinczarkowski", False, {})
        self.assertEqual((values["repos"], values["stars"], values["commits"]), (1, 2, 1))
        self.assertNotIn("priv", client.history_calls)

    def test_collect_reposAndStars_countOnlyOwnedRepositories(self):
        client = FakeClient([repo("mine", None, stars=1), repo("theirs", None, owner="someone", stars=9)])
        values, _ = gs.collect(client, "martinczarkowski", False, {})
        self.assertEqual((values["repos"], values["stars"]), (1, 1))

    def test_collect_contributed_unitesReposWithCommitsAndGitHubList(self):
        client = FakeClient([repo("a", "h1"), repo("empty", None)], contributed=[repo("ext", "h3", owner="org")],
                            histories={"a": [commit("c1")], "ext": []})
        values, _ = gs.collect(client, "martinczarkowski", False, {})
        self.assertEqual(values["contributed"], 2)

    def test_collect_unchangedHead_doesNotQueryHistoryAgain(self):
        histories = {"a": [commit("c1")]}
        cache = {}
        gs.collect(FakeClient([repo("a", "h1")], histories=histories), "martinczarkowski", False, cache)
        second = FakeClient([repo("a", "h1")], histories=histories)
        values, _ = gs.collect(second, "martinczarkowski", False, cache)
        self.assertEqual(second.history_calls, [])
        self.assertEqual(values["commits"], 1)

    def test_collect_newCommits_fetchesOnlyUntilKnownCommit(self):
        cache = {}
        gs.collect(FakeClient([repo("a", "h1")], histories={"a": [commit(f"old{i}") for i in range(120)]}),
                   "martinczarkowski", False, cache)
        client = FakeClient([repo("a", "h2")], histories={"a": [commit("new")] + [commit(f"old{i}") for i in range(120)]})
        values, _ = gs.collect(client, "martinczarkowski", False, cache)
        self.assertEqual(client.history_calls, ["a"])
        self.assertEqual(values["commits"], 121)

    def test_collect_rewrittenHistory_rescansBranch(self):
        cache = {}
        gs.collect(FakeClient([repo("a", "h1")], histories={"a": [commit("c2"), commit("c1")]}),
                   "martinczarkowski", False, cache)
        client = FakeClient([repo("a", "h2")], histories={"a": [commit("c3"), commit("c1")]})   # c2 force-pushed away
        values, _ = gs.collect(client, "martinczarkowski", False, cache)
        self.assertEqual(values["commits"], 2)

    def test_collect_historyFailsWithoutCache_leavesCommitMetricsUnknown(self):
        client = FakeClient([repo("a", "h1")], failing={"a"})
        values, errors = gs.collect(client, "martinczarkowski", False, {})
        self.assertIsNone(values["commits"])
        self.assertIsNone(values["additions"])
        self.assertEqual(values["repos"], 1)
        self.assertEqual(len(errors), 1)

    def test_collect_privateRepoFails_errorDoesNotRevealName(self):
        client = FakeClient([repo("secret-project", "h1", private=True)], failing={"secret-project"})
        _, errors = gs.collect(client, "martinczarkowski", True, {})
        self.assertNotIn("secret-project", errors[0])

    def test_collect_historyFailsWithCache_keepsCachedCommits(self):
        cache = {}
        gs.collect(FakeClient([repo("a", "h1")], histories={"a": [commit("c1")]}), "martinczarkowski", False, cache)
        values, errors = gs.collect(FakeClient([repo("a", "h2")], failing={"a"}), "martinczarkowski", False, cache)
        self.assertEqual(values["commits"], 1)
        self.assertEqual(len(errors), 1)


class MergeStatsTests(unittest.TestCase):
    def test_merge_stats_failedMetric_keepsPreviousValue(self):
        stats, failed = today.merge_stats({"scope": "public", "commits": 25}, {"commits": None, "repos": 7}, "public")
        self.assertEqual((stats["commits"], stats["repos"], failed), (25, 7, ["commits"]))

    def test_merge_stats_scopeChanged_doesNotReuseOtherScopeValue(self):
        stats, _ = today.merge_stats({"scope": "public", "commits": 25}, {"commits": None}, "all")
        self.assertIsNone(stats["commits"])


if __name__ == "__main__":
    unittest.main()
