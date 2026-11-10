import io
import json
import sys
import time
import unittest
import urllib.error
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import github_stats as gs  # noqa: E402

USER_ID = "U_me"
LOGIN = "martinczarkowski"


def commit(oid, additions=10, deletions=2, parents=1):
    return {"oid": oid, "additions": additions, "deletions": deletions, "parents": {"totalCount": parents}}


def repo(repo_id, head, owner=LOGIN, private=False, stars=0, branch="main"):
    return {"id": repo_id, "nameWithOwner": f"{owner}/{repo_id}", "isPrivate": private, "stargazerCount": stars,
            "owner": {"login": owner},
            "defaultBranchRef": {"name": branch, "target": {"oid": head}} if head else None}


class FakeClient:
    """Answers the module's queries from in-memory data; histories are newest first."""

    def __init__(self, affiliated, contributed=(), histories=None, failing=(), malformed=(), page_size=50,
                 user_fails=False, lists_fail=False):
        self.affiliated, self.contributed = list(affiliated), list(contributed)
        self.histories, self.failing, self.malformed = histories or {}, set(failing), set(malformed)
        self.page_size, self.user_fails, self.lists_fail = page_size, user_fails, lists_fail
        self.history_calls, self.list_calls = [], 0

    def graphql(self, query, variables):
        if query is gs.USER_QUERY:
            if self.user_fails:
                raise gs.GitHubError("Bad credentials", kind="auth")
            return {"user": {"id": USER_ID, "createdAt": "2019-12-13T13:46:19Z", "followers": {"totalCount": 3}}}
        if query in (gs.AFFILIATED_QUERY, gs.CONTRIBUTED_QUERY):
            self.list_calls += 1
            if self.lists_fail:
                raise gs.GitHubError("HTTP 502 Bad Gateway", kind="http")
            nodes = self.affiliated if query is gs.AFFILIATED_QUERY else self.contributed
            if variables["privacy"] == "PUBLIC":
                nodes = [node for node in nodes if not node["isPrivate"]]
            start = int(variables["cursor"] or 0)
            page = nodes[start:start + self.page_size]
            has_next = start + self.page_size < len(nodes)
            return {"user": {"connection": {"nodes": page, "pageInfo": {
                "hasNextPage": has_next, "endCursor": str(start + self.page_size)}}}}
        repo_id = variables["id"]
        self.history_calls.append(repo_id)
        if repo_id in self.failing:
            raise gs.GitHubError("HTTP 502 for martinczarkowski/" + repo_id, kind="http")
        if repo_id in self.malformed:
            return {"node": {"defaultBranchRef": {"target": {}}}}
        nodes = self.histories[repo_id]
        start = int(variables["cursor"] or 0)
        page = nodes[start:start + variables["first"]]
        has_next = start + variables["first"] < len(nodes)
        history = {"totalCount": len(nodes), "nodes": page,
                   "pageInfo": {"hasNextPage": has_next, "endCursor": str(start + variables["first"])}}
        return {"node": {"defaultBranchRef": {"target": {"history": history}}}}


def collect(client, cache=None, include_private=False, exclude_owners=()):
    return gs.collect(client, LOGIN, include_private, {} if cache is None else cache, exclude_owners)


class CollectAggregationTests(unittest.TestCase):
    def test_collect_forkSharesCommits_countsEachCommitOnce(self):
        client = FakeClient([repo("a", "h1"), repo("fork", "h2")],
                            histories={"a": [commit("c1"), commit("c2")], "fork": [commit("c1"), commit("c2")]})
        result = collect(client)
        self.assertEqual(result.errors, [])
        self.assertEqual(result.status, "complete")
        values = result.values
        self.assertEqual((values["commits"], values["additions"], values["deletions"]), (2, 20, 4))

    def test_collect_mergeCommit_excludedFromCommitsAndLines(self):
        client = FakeClient([repo("a", "h1")], histories={"a": [commit("m", 500, 500, parents=2), commit("c1")]})
        values = collect(client).values
        self.assertEqual((values["commits"], values["additions"]), (1, 10))

    def test_collect_publicMode_ignoresPrivateRepositories(self):
        client = FakeClient([repo("pub", "h1", stars=2), repo("priv", "h2", private=True, stars=5)],
                            histories={"pub": [commit("c1")], "priv": [commit("c2")]})
        values = collect(client).values
        self.assertEqual((values["repos"], values["stars"], values["commits"]), (1, 2, 1))
        self.assertNotIn("priv", client.history_calls)

    def test_collect_reposAndStars_countOnlyOwnedRepositories(self):
        client = FakeClient([repo("mine", None, stars=1), repo("theirs", None, owner="someone", stars=9)])
        values = collect(client).values
        self.assertEqual((values["repos"], values["stars"]), (1, 1))

    def test_collect_contributed_unitesReposWithCommitsAndGitHubList(self):
        client = FakeClient([repo("a", "h1"), repo("empty", None)],
                            contributed=[repo("ext", "h3", owner="org"), repo("a", "h1")],
                            histories={"a": [commit("c1")], "ext": []})
        self.assertEqual(collect(client).values["contributed"], 2)

    def test_collect_excludedOwner_reposNeitherScannedNorCounted(self):
        client = FakeClient([repo("a", "h1"), repo("work", "h2", owner="Some-Org")],
                            contributed=[repo("work", "h2", owner="Some-Org"), repo("ext", "h3", owner="other")],
                            histories={"a": [commit("c1")], "work": [commit("c2")], "ext": []})
        values = collect(client, exclude_owners=["some-org"]).values
        self.assertEqual((values["commits"], values["contributed"]), (1, 2))
        self.assertNotIn("work", client.history_calls)

    def test_collect_paginatedRepositoryList_readsAllPages(self):
        repos = [repo(f"r{i}", f"h{i}") for i in range(5)]
        client = FakeClient(repos, histories={f"r{i}": [commit(f"c{i}")] for i in range(5)}, page_size=2)
        values = collect(client).values
        self.assertEqual((values["repos"], values["commits"]), (5, 5))

    def test_collect_paginatedHistory_readsAllCommits(self):
        client = FakeClient([repo("a", "h1")], histories={"a": [commit(f"c{i}") for i in range(130)]})
        self.assertEqual(collect(client).values["commits"], 130)


class CollectCacheTests(unittest.TestCase):
    def test_collect_unchangedHead_doesNotQueryHistoryAgain(self):
        histories = {"a": [commit("c1")]}
        cache = {}
        collect(FakeClient([repo("a", "h1")], histories=histories), cache)
        second = FakeClient([repo("a", "h1")], histories=histories)
        result = collect(second, cache)
        self.assertEqual(second.history_calls, [])
        self.assertEqual((result.values["commits"], result.status), (1, "complete"))

    def test_collect_newCommits_fetchesOnlyUntilKnownCommit(self):
        cache = {}
        old = [commit(f"old{i}") for i in range(120)]
        collect(FakeClient([repo("a", "h1")], histories={"a": old}), cache)
        client = FakeClient([repo("a", "h2")], histories={"a": [commit("new")] + old})
        self.assertEqual(collect(client, cache).values["commits"], 121)
        self.assertEqual(client.history_calls, ["a"])

    def test_collect_rewrittenHistory_rescansBranch(self):
        cache = {}
        collect(FakeClient([repo("a", "h1")], histories={"a": [commit("c2"), commit("c1")]}), cache)
        client = FakeClient([repo("a", "h2")], histories={"a": [commit("c3"), commit("c1")]})   # c2 force-pushed away
        self.assertEqual(collect(client, cache).values["commits"], 2)

    def test_collect_defaultBranchChanged_rescansWithoutReusingOldBranchCommits(self):
        cache = {}
        collect(FakeClient([repo("a", "h1", branch="main")],
                           histories={"a": [commit("a1", 1), commit("b1", 100)]}), cache)
        # New default branch shares a1, lacks b1 and has an older z1: same commit count as before.
        client = FakeClient([repo("a", "h2", branch="dev")],
                            histories={"a": [commit("x1", 1), commit("a1", 1), commit("z1", 1)]})
        values = collect(client, cache).values
        self.assertEqual((values["commits"], values["additions"]), (3, 3))

    def test_collect_repositoryRemoved_dropsItFromCountsAndCache(self):
        cache = {}
        collect(FakeClient([repo("a", "h1"), repo("b", "h2")],
                           histories={"a": [commit("c1")], "b": [commit("c2")]}), cache)
        values = collect(FakeClient([repo("a", "h1")], histories={"a": [commit("c1")]}), cache).values
        self.assertEqual((values["repos"], values["commits"]), (1, 1))
        self.assertEqual(set(cache["repos"]), {"a"})

    def test_collect_privateToPublicMode_resetsCacheAndSkipsPrivateRepos(self):
        cache = {}
        affiliated = [repo("pub", "h1"), repo("priv", "h2", private=True)]
        histories = {"pub": [commit("c1")], "priv": [commit("c2")]}
        collect(FakeClient(affiliated, histories=histories), cache, include_private=True)
        self.assertIn("priv", cache["repos"])
        values = collect(FakeClient(affiliated, histories=histories), cache, include_private=False).values
        self.assertEqual(values["commits"], 1)
        self.assertNotIn("priv", cache["repos"])

    def test_collect_corruptedCacheStructure_startsOver(self):
        cache = {"version": gs.CACHE_VERSION, "user_id": USER_ID, "include_private": False, "repos": []}
        result = collect(FakeClient([repo("a", "h1")], histories={"a": [commit("c1")]}), cache)
        self.assertEqual((result.values["commits"], result.status), (1, "complete"))

    def test_collect_corruptedCacheEntry_rescansThatRepository(self):
        cache = {"version": gs.CACHE_VERSION, "user_id": USER_ID, "include_private": False,
                 "repos": {"a": {"head": "h1", "commits": {"c1": "garbage"}}}}
        client = FakeClient([repo("a", "h1")], histories={"a": [commit("c1")]})
        result = collect(client, cache)
        self.assertEqual(client.history_calls, ["a"])
        self.assertEqual(result.values["commits"], 1)

    def test_load_cache_corruptedFile_returnsEmptyCache(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "stats_cache.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(gs.load_cache(path), {})

    def test_save_cache_roundTrip_returnsSameData(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache" / "stats_cache.json"
            gs.save_cache(path, {"version": 2, "repos": {}})
            self.assertEqual(gs.load_cache(path), {"version": 2, "repos": {}})


class CollectFailureTests(unittest.TestCase):
    def test_collect_historyFailsWithCache_marksCommitMetricsStale(self):
        cache = {}
        collect(FakeClient([repo("a", "h1")], histories={"a": [commit("c1")]}), cache)
        result = collect(FakeClient([repo("a", "h2")], failing={"a"}), cache)
        self.assertEqual(result.values["commits"], 1)
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.stale, {"commits", "additions", "deletions", "contributed"})
        self.assertEqual(len(result.errors), 1)

    def test_collect_historyFailsWithoutCache_leavesCommitMetricsUnknown(self):
        result = collect(FakeClient([repo("a", "h1")], failing={"a"}))
        self.assertIsNone(result.values["commits"])
        self.assertIsNone(result.values["additions"])
        self.assertEqual(result.values["repos"], 1)
        self.assertEqual(result.status, "partial")

    def test_collect_malformedHistory_treatedAsRepositoryFailure(self):
        result = collect(FakeClient([repo("a", "h1")], malformed={"a"}))
        self.assertIsNone(result.values["commits"])
        self.assertEqual(result.status, "partial")
        self.assertIn("malformed", result.errors[0])

    def test_collect_recoveryAfterFailure_reportsCompleteFreshValues(self):
        cache = {}
        collect(FakeClient([repo("a", "h1")], histories={"a": [commit("c1")]}), cache)
        collect(FakeClient([repo("a", "h2")], failing={"a"}), cache)
        result = collect(FakeClient([repo("a", "h2")], histories={"a": [commit("c2"), commit("c1")]}), cache)
        self.assertEqual((result.status, result.stale, result.values["commits"]), ("complete", set(), 2))

    def test_collect_userQueryFails_reportsFailedWithoutValues(self):
        client = FakeClient([repo("a", "h1")], user_fails=True)
        result = collect(client)
        self.assertEqual(result.status, "failed")
        self.assertTrue(all(value is None for value in result.values.values()))
        self.assertEqual(client.list_calls, 0)

    def test_collect_repositoryListFails_keepsUserMetricsOnly(self):
        result = collect(FakeClient([repo("a", "h1")], lists_fail=True))
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.values["followers"], 3)
        self.assertIsNone(result.values["repos"])

    def test_collect_privateRepoFails_errorDoesNotRevealNameOrApiMessage(self):
        client = FakeClient([repo("secret-project", "h1", private=True)], failing={"secret-project"})
        result = collect(client, include_private=True)
        self.assertEqual(len(result.errors), 1)
        self.assertNotIn("secret-project", result.errors[0])

    def test_collect_privateMode_errorsNeverIncludeApiMessages(self):
        client = FakeClient([repo("a", "h1")], lists_fail=True)
        result = collect(client, include_private=True)
        self.assertNotIn("Bad Gateway", " ".join(result.errors))


def http_error(code, headers=None, body=b"{}"):
    return urllib.error.HTTPError(gs.API_URL, code, "error", headers or {}, io.BytesIO(body))


def ok(payload):
    response = mock.MagicMock()
    response.__enter__.return_value = io.BytesIO(json.dumps(payload).encode())
    return response


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.sleeps = []
        self.client = gs.GitHubClient("token", sleep=self.sleeps.append)

    def call(self, *responses):
        with mock.patch.object(gs.urllib.request, "urlopen", side_effect=list(responses)) as urlopen:
            try:
                return self.client.graphql("query", {}), urlopen.call_count
            except gs.GitHubError as err:
                return err, urlopen.call_count

    def test_graphql_success_returnsData(self):
        result, calls = self.call(ok({"data": {"x": 1}}))
        self.assertEqual((result, calls), ({"x": 1}, 1))

    def test_graphql_http502ThenSuccess_retriesWithBackoff(self):
        result, calls = self.call(http_error(502), ok({"data": {"x": 1}}))
        self.assertEqual((result, calls, len(self.sleeps)), ({"x": 1}, 2, 1))

    def test_graphql_timeoutEveryAttempt_failsAndOpensCircuit(self):
        error, calls = self.call(*[TimeoutError("timed out")] * gs.RETRIES)
        self.assertEqual((error.kind, calls), ("network", gs.RETRIES))
        error, calls = self.call(ok({"data": {}}))
        self.assertEqual((error.kind, calls), ("network", 0))

    def test_graphql_connectionReset_retried(self):
        result, calls = self.call(ConnectionResetError(), ok({"data": {"x": 1}}))
        self.assertEqual((result, calls), ({"x": 1}, 2))

    def test_graphql_rateLimitResetSoon_waitsThenRetries(self):
        headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(time.time()) + 30)}
        result, calls = self.call(http_error(403, headers), ok({"data": {"x": 1}}))
        self.assertEqual((result, calls), ({"x": 1}, 2))
        self.assertTrue(25 <= self.sleeps[0] <= gs.MAX_RATE_LIMIT_WAIT)

    def test_graphql_rateLimitResetFarAway_failsFastAndOpensCircuit(self):
        headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(time.time()) + 3600)}
        error, calls = self.call(http_error(403, headers))
        self.assertEqual((error.kind, calls, self.sleeps), ("rate_limited", 1, []))
        self.assertIsNotNone(self.client.outage)

    def test_graphql_secondaryRateLimit_honoursRetryAfter(self):
        result, _ = self.call(http_error(403, {"Retry-After": "7"}), ok({"data": {"x": 1}}))
        self.assertEqual((result, self.sleeps), ({"x": 1}, [7.0]))

    def test_graphql_forbiddenWithoutRateLimit_failsWithoutOpeningCircuit(self):
        error, calls = self.call(http_error(403))
        self.assertEqual((error.kind, calls), ("http", 1))
        self.assertIsNone(self.client.outage)

    def test_graphql_badCredentials_failsFastAndOpensCircuit(self):
        error, calls = self.call(http_error(401))
        self.assertEqual((error.kind, calls), ("auth", 1))
        self.assertIsNotNone(self.client.outage)

    def test_graphql_invalidJson_retriedThenMalformed(self):
        bad = mock.MagicMock()
        bad.__enter__.return_value = io.BytesIO(b"<html>Unicorn!</html>")
        error, calls = self.call(*[bad] * gs.RETRIES)
        self.assertEqual((error.kind, calls), ("malformed", gs.RETRIES))

    def test_graphql_missingData_malformedWithoutRetry(self):
        error, calls = self.call(ok({"unexpected": True}))
        self.assertEqual((error.kind, calls), ("malformed", 1))

    def test_graphql_graphqlRateLimitedError_waitsAndRetries(self):
        result, _ = self.call(ok({"errors": [{"type": "RATE_LIMITED", "message": "slow down"}]}), ok({"data": {"x": 1}}))
        self.assertEqual((result, self.sleeps), ({"x": 1}, [60]))

    def test_graphql_graphqlNotFound_failsWithoutOpeningCircuit(self):
        error, _ = self.call(ok({"errors": [{"type": "NOT_FOUND", "message": "Could not resolve"}]}))
        self.assertEqual(error.kind, "graphql")
        self.assertIsNone(self.client.outage)


class OutageTests(unittest.TestCase):
    def test_collect_apiOutageDuringScan_stopsQueryingRemainingRepositories(self):
        user = ok({"data": {"user": {"id": USER_ID, "createdAt": "2019-12-13T13:46:19Z",
                                     "followers": {"totalCount": 3}}}})
        listing = {"data": {"user": {"connection": {"nodes": [repo("a", "h1"), repo("b", "h2"), repo("c", "h3")],
                                                   "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}
        empty = {"data": {"user": {"connection": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}
        responses = [user, ok(listing), ok(empty)] + [http_error(502)] * gs.RETRIES
        client = gs.GitHubClient("token", sleep=lambda _: None)
        with mock.patch.object(gs.urllib.request, "urlopen", side_effect=responses) as urlopen:
            result = gs.collect(client, LOGIN, False, {})
        self.assertEqual(urlopen.call_count, 3 + gs.RETRIES)
        self.assertEqual(result.status, "partial")
        self.assertIsNone(result.values["commits"])


if __name__ == "__main__":
    unittest.main()
