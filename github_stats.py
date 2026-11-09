"""Collect the GitHub statistics shown on the profile card (GraphQL API, standard library only).

Definitions (see SETUP.md for the limitations):
- repos       repositories owned by the account
- stars       stargazers summed over the owned repositories
- followers   current follower count
- commits     non-merge commits authored by the account on the default branch of every
              scanned repository, each commit counted once even if it appears in forks
- additions / deletions   summed over those same commits
- contributed repositories with at least one such commit, plus GitHub's own
              `repositoriesContributedTo` list (commits, PRs, reviews, issues, recently)

Scanned repositories = owned / collaborator / organisation-member repositories plus the
`repositoriesContributedTo` list. Private repositories only when include_private is set.
The per-repository commit cache makes daily runs incremental: a repository whose default
branch head did not move is not queried again.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path

API_URL = "https://api.github.com/graphql"
CACHE_VERSION = 1
HISTORY_PAGE_SIZE = 50   # commits with additions/deletions are expensive; 100 risks 502 timeouts
MAX_RATE_LIMIT_WAIT = 120

METRICS = ("created_at", "followers", "repos", "stars", "contributed", "commits", "additions", "deletions")

USER_QUERY = """
query($login: String!) {
  user(login: $login) { id createdAt followers { totalCount } }
}"""

REPO_FIELDS = """
fragment repo on Repository {
  id nameWithOwner isPrivate stargazerCount owner { login }
  defaultBranchRef { target { ... on Commit { oid } } }
}"""

AFFILIATED_QUERY = """
query($login: String!, $cursor: String, $privacy: RepositoryPrivacy) {
  user(login: $login) {
    connection: repositories(first: 50, after: $cursor, privacy: $privacy,
                             ownerAffiliations: [OWNER, COLLABORATOR, ORGANIZATION_MEMBER]) {
      nodes { ...repo }
      pageInfo { hasNextPage endCursor }
    }
  }
}""" + REPO_FIELDS

CONTRIBUTED_QUERY = """
query($login: String!, $cursor: String, $privacy: RepositoryPrivacy) {
  user(login: $login) {
    connection: repositoriesContributedTo(first: 50, after: $cursor, privacy: $privacy,
                                          includeUserRepositories: true,
                                          contributionTypes: [COMMIT, PULL_REQUEST, PULL_REQUEST_REVIEW, ISSUE, REPOSITORY]) {
      nodes { ...repo }
      pageInfo { hasNextPage endCursor }
    }
  }
}""" + REPO_FIELDS

HISTORY_QUERY = """
query($id: ID!, $author: ID!, $cursor: String, $first: Int!) {
  node(id: $id) {
    ... on Repository {
      defaultBranchRef {
        target {
          ... on Commit {
            history(first: $first, after: $cursor, author: {id: $author}) {
              totalCount
              nodes { oid additions deletions parents { totalCount } }
              pageInfo { hasNextPage endCursor }
            }
          }
        }
      }
    }
  }
}"""


class GitHubError(Exception):
    pass


class GitHubClient:
    def __init__(self, token: str, retries: int = 4, sleep=time.sleep):
        self._token = token
        self._retries = retries
        self._sleep = sleep

    def graphql(self, query: str, variables: dict) -> dict:
        body = json.dumps({"query": query, "variables": variables}).encode()
        for attempt in range(self._retries):
            request = urllib.request.Request(API_URL, data=body, headers={
                "Authorization": f"bearer {self._token}",
                "Content-Type": "application/json",
                "User-Agent": "martinczarkowski-profile-readme",
            })
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    payload = json.load(response)
            except urllib.error.HTTPError as err:
                wait = _rate_limit_wait(err.headers) if err.code in (403, 429) else None
                if wait is not None and wait <= MAX_RATE_LIMIT_WAIT:
                    self._sleep(wait)
                    continue
                if err.code in (502, 503, 504):
                    self._sleep(5 * 2 ** attempt)
                    continue
                raise GitHubError(f"HTTP {err.code} {err.reason}") from None
            except (urllib.error.URLError, TimeoutError):
                self._sleep(5 * 2 ** attempt)
                continue
            errors = payload.get("errors")
            if errors:
                if any(error.get("type") == "RATE_LIMITED" for error in errors):
                    self._sleep(60)
                    continue
                raise GitHubError("; ".join(error.get("message", "unknown error") for error in errors))
            return payload["data"]
        raise GitHubError(f"no successful response after {self._retries} attempts")


def _rate_limit_wait(headers) -> float | None:
    """Seconds to wait according to Retry-After or the rate-limit reset header."""
    if headers.get("Retry-After"):
        try:
            return float(headers["Retry-After"])
        except ValueError:
            return max(0.0, parsedate_to_datetime(headers["Retry-After"]).timestamp() - time.time())
    if headers.get("X-RateLimit-Remaining") == "0" and headers.get("X-RateLimit-Reset"):
        return max(0.0, float(headers["X-RateLimit-Reset"]) - time.time()) + 1
    return None


def load_cache(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_cache(path: Path, cache: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, separators=(",", ":"), sort_keys=True), encoding="utf-8")


def _paginate(client: GitHubClient, query: str, variables: dict) -> list[dict]:
    nodes, cursor = [], None
    while True:
        user = client.graphql(query, {**variables, "cursor": cursor})["user"]
        if user is None:
            raise GitHubError("user not found")
        connection = user["connection"]
        nodes += [node for node in connection["nodes"] if node]
        if not connection["pageInfo"]["hasNextPage"]:
            return nodes
        cursor = connection["pageInfo"]["endCursor"]


def fetch_history(client: GitHubClient, repo_id: str, user_id: str, known: dict) -> tuple[dict, int]:
    """Commits authored by user_id on the default branch, newest first, stopping at a known one.

    Returns ({oid: [additions, deletions, is_merge]}, total authored commits on the branch).
    """
    commits, cursor = {}, None
    while True:
        variables = {"id": repo_id, "author": user_id, "cursor": cursor, "first": HISTORY_PAGE_SIZE}
        repo = client.graphql(HISTORY_QUERY, variables)["node"]
        if not repo or not repo["defaultBranchRef"]:
            return {}, 0
        history = repo["defaultBranchRef"]["target"]["history"]
        for node in history["nodes"]:
            if node["oid"] in known:
                return commits, history["totalCount"]
            commits[node["oid"]] = [node["additions"], node["deletions"], int(node["parents"]["totalCount"] > 1)]
        if not history["pageInfo"]["hasNextPage"]:
            return commits, history["totalCount"]
        cursor = history["pageInfo"]["endCursor"]


def update_repo(client: GitHubClient, repo_id: str, head: str, user_id: str, cached: dict | None) -> dict:
    """Return the cache entry for one repository, querying only commits newer than the cache."""
    if cached and cached["head"] == head:
        return cached
    known = cached["commits"] if cached else {}
    fresh, total = fetch_history(client, repo_id, user_id, known)
    commits = {**known, **fresh}
    if len(commits) != total:   # history was rewritten (force push, rebase): rescan the branch
        commits, _ = fetch_history(client, repo_id, user_id, {})
    return {"head": head, "commits": commits}


def collect(client: GitHubClient, login: str, include_private: bool, cache: dict) -> tuple[dict, list[str]]:
    """Fetch all metrics. A metric that could not be determined reliably is None.

    `cache` is updated in place. Error messages never contain private repository names,
    because workflow logs of a public repository are public.
    """
    values: dict = dict.fromkeys(METRICS)
    errors: list[str] = []
    try:
        user = client.graphql(USER_QUERY, {"login": login})["user"]
        if user is None:
            raise GitHubError(f"user {login} not found")
    except GitHubError as err:
        return values, [f"user query failed: {err}"]
    values["created_at"] = user["createdAt"]
    values["followers"] = user["followers"]["totalCount"]

    scope = {"version": CACHE_VERSION, "user_id": user["id"], "include_private": include_private}
    if any(cache.get(key) != value for key, value in scope.items()):
        cache.clear()
        cache.update(scope, repos={})

    privacy = None if include_private else "PUBLIC"
    try:
        affiliated = _paginate(client, AFFILIATED_QUERY, {"login": login, "privacy": privacy})
        contributed_to = _paginate(client, CONTRIBUTED_QUERY, {"login": login, "privacy": privacy})
    except GitHubError as err:
        return values, [f"repository list failed: {err}"]
    if not include_private:   # never trust the privacy filter alone
        affiliated = [repo for repo in affiliated if not repo["isPrivate"]]
        contributed_to = [repo for repo in contributed_to if not repo["isPrivate"]]

    owned = [repo for repo in affiliated if repo["owner"]["login"].lower() == login.lower()]
    values["repos"] = len(owned)
    values["stars"] = sum(repo["stargazerCount"] for repo in owned)

    scanned: dict[str, dict] = {}
    complete = True
    for repo in {repo["id"]: repo for repo in affiliated + contributed_to}.values():
        head = ((repo.get("defaultBranchRef") or {}).get("target") or {}).get("oid")
        if head is None:   # empty repository or no default branch
            continue
        cached = cache["repos"].get(repo["id"])
        try:
            scanned[repo["id"]] = update_repo(client, repo["id"], head, user["id"], cached)
        except GitHubError as err:
            name = "a private repository" if repo["isPrivate"] else repo["nameWithOwner"]
            errors.append(f"commit history of {name} failed: {err}")
            if cached:
                scanned[repo["id"]] = cached   # last known data is still valid
            else:
                complete = False
    cache["repos"] = scanned   # repositories that disappeared or became invisible drop out

    if complete:
        commits: dict[str, tuple[int, int]] = {}
        with_commits: set[str] = set()
        for repo_id, entry in scanned.items():
            for oid, (additions, deletions, is_merge) in entry["commits"].items():
                if not is_merge:
                    commits[oid] = (additions, deletions)
                    with_commits.add(repo_id)
        values["commits"] = len(commits)
        values["additions"] = sum(additions for additions, _ in commits.values())
        values["deletions"] = sum(deletions for _, deletions in commits.values())
        values["contributed"] = len(with_commits | {repo["id"] for repo in contributed_to})
    return values, errors
