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

collect() never raises for API problems. It returns a CollectResult whose `status` tells
complete / partial / failed apart, and whose `stale` set names metrics computed from cached
data that could not be refreshed.
"""
from __future__ import annotations

import http.client
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from pathlib import Path

API_URL = "https://api.github.com/graphql"
CACHE_VERSION = 2
HISTORY_PAGE_SIZE = 50   # commits with additions/deletions are expensive; 100 risks 502 timeouts
RETRIES = 4
MAX_RATE_LIMIT_WAIT = 120

METRICS = ("created_at", "followers", "repos", "stars", "contributed", "commits", "additions", "deletions")
COMMIT_METRICS = {"contributed", "commits", "additions", "deletions"}

USER_QUERY = """
query($login: String!) {
  user(login: $login) { id createdAt followers { totalCount } }
}"""

REPO_FIELDS = """
fragment repo on Repository {
  id nameWithOwner isPrivate stargazerCount owner { login }
  defaultBranchRef { name target { ... on Commit { oid } } }
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
    """An API failure. `kind` is safe to log; the message may contain API text.

    `trip` marks failures that make further requests pointless (outage, bad token, long
    rate limit): the client then fails every following call without contacting GitHub.
    """

    def __init__(self, message: str, kind: str = "http", trip: bool = False):
        super().__init__(message)
        self.kind = kind
        self.trip = trip


@dataclass
class CollectResult:
    values: dict
    stale: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)
    user_failed: bool = False

    @property
    def status(self) -> str:
        if self.user_failed:
            return "failed"
        if self.errors or self.stale or any(value is None for value in self.values.values()):
            return "partial"
        return "complete"


class GitHubClient:
    def __init__(self, token: str, retries: int = RETRIES, sleep=time.sleep):
        self._token = token
        self._retries = retries
        self._sleep = sleep
        self.outage: GitHubError | None = None

    def graphql(self, query: str, variables: dict) -> dict:
        if self.outage:
            raise GitHubError(f"skipped after earlier failure: {self.outage}", kind=self.outage.kind)
        try:
            return self._request(query, variables)
        except GitHubError as err:
            if err.trip:
                self.outage = err
            raise

    def _backoff(self, attempt: int) -> None:
        if attempt < self._retries - 1:
            self._sleep(5 * 2 ** attempt)

    def _request(self, query: str, variables: dict) -> dict:
        body = json.dumps({"query": query, "variables": variables}).encode()
        last = GitHubError("no response", kind="network", trip=True)
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
                code, reason, headers = err.code, err.reason, err.headers or {}
                err.close()   # release the connection
                if code == 401:
                    raise GitHubError("bad credentials (HTTP 401)", kind="auth", trip=True) from None
                wait = _rate_limit_wait(headers) if code in (403, 429) else None
                if wait is not None:
                    if wait > MAX_RATE_LIMIT_WAIT:
                        raise GitHubError(f"rate limited for {wait:.0f}s", kind="rate_limited", trip=True) from None
                    self._sleep(wait)
                    continue
                if code >= 500:
                    last = GitHubError(f"HTTP {code}", kind="network", trip=True)
                    self._backoff(attempt)
                    continue
                raise GitHubError(f"HTTP {code} {reason}", kind="http") from None
            except (OSError, http.client.HTTPException) as err:   # timeouts, resets, DNS, TLS
                last = GitHubError(type(err).__name__, kind="network", trip=True)
                self._backoff(attempt)
                continue
            except ValueError:   # not JSON, e.g. an HTML error page from a proxy
                last = GitHubError("invalid JSON response", kind="malformed", trip=True)
                self._backoff(attempt)
                continue
            if not isinstance(payload, dict):
                raise GitHubError("unexpected response shape", kind="malformed")
            errors = payload.get("errors")
            if errors:
                if any(isinstance(error, dict) and error.get("type") == "RATE_LIMITED" for error in errors):
                    self._sleep(60)
                    continue
                raise GitHubError("; ".join(str(error.get("message", error)) if isinstance(error, dict) else str(error)
                                            for error in errors), kind="graphql")
            if not isinstance(payload.get("data"), dict):
                raise GitHubError("response without data", kind="malformed")
            return payload["data"]
        raise last


def _rate_limit_wait(headers) -> float | None:
    """Seconds to wait according to Retry-After or the rate-limit reset header, else None."""
    try:
        if headers.get("Retry-After"):
            try:
                return float(headers["Retry-After"])
            except ValueError:
                return max(0.0, parsedate_to_datetime(headers["Retry-After"]).timestamp() - time.time())
        if headers.get("X-RateLimit-Remaining") == "0" and headers.get("X-RateLimit-Reset"):
            return max(0.0, float(headers["X-RateLimit-Reset"]) - time.time()) + 1
    except (TypeError, ValueError):
        return None
    return None


def load_cache(path: Path) -> dict:
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return cache if isinstance(cache, dict) else {}


def save_cache(path: Path, cache: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(cache, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def _valid_entry(entry) -> bool:
    return (isinstance(entry, dict) and isinstance(entry.get("head"), str) and isinstance(entry.get("branch"), str)
            and isinstance(entry.get("commits"), dict)
            and all(isinstance(value, list) and len(value) == 3 and all(isinstance(n, int) for n in value)
                    for value in entry["commits"].values()))


def _paginate(client: GitHubClient, query: str, variables: dict) -> list[dict]:
    nodes, cursor = [], None
    while True:
        data = client.graphql(query, {**variables, "cursor": cursor})
        try:
            connection = data["user"]["connection"]
            nodes += [node for node in connection["nodes"] if node]
            if not connection["pageInfo"]["hasNextPage"]:
                return nodes
            cursor = connection["pageInfo"]["endCursor"]
        except (KeyError, TypeError):
            raise GitHubError("malformed repository list", kind="malformed") from None


def fetch_history(client: GitHubClient, repo_id: str, user_id: str, known: dict) -> tuple[dict, int]:
    """Commits authored by user_id on the default branch, newest first, stopping at a known one.

    Returns ({oid: [additions, deletions, is_merge]}, total authored commits on the branch).
    """
    commits, cursor = {}, None
    while True:
        variables = {"id": repo_id, "author": user_id, "cursor": cursor, "first": HISTORY_PAGE_SIZE}
        data = client.graphql(HISTORY_QUERY, variables)
        try:
            repo = data["node"]
            if not repo or not repo["defaultBranchRef"]:
                return {}, 0
            history = repo["defaultBranchRef"]["target"]["history"]
            for node in history["nodes"]:
                if node["oid"] in known:
                    return commits, history["totalCount"]
                commits[node["oid"]] = [int(node["additions"]), int(node["deletions"]),
                                        int(node["parents"]["totalCount"] > 1)]
            if not history["pageInfo"]["hasNextPage"]:
                return commits, history["totalCount"]
            cursor = history["pageInfo"]["endCursor"]
        except (KeyError, TypeError, ValueError):
            raise GitHubError("malformed commit history", kind="malformed") from None


def update_repo(client: GitHubClient, repo_id: str, head: str, branch: str, user_id: str,
                cached: dict | None) -> dict:
    """Return the cache entry for one repository, querying only commits newer than the cache."""
    if cached and cached["head"] == head and cached["branch"] == branch:
        return cached
    known = cached["commits"] if cached and cached["branch"] == branch else {}   # new default branch: rescan
    fresh, total = fetch_history(client, repo_id, user_id, known)
    commits = {**known, **fresh}
    if len(commits) != total:   # history was rewritten (force push, rebase): rescan the branch
        commits, _ = fetch_history(client, repo_id, user_id, {})
    return {"head": head, "branch": branch, "commits": commits}


def collect(client: GitHubClient, login: str, include_private: bool, cache: dict) -> CollectResult:
    """Fetch all metrics. A metric that could not be determined reliably is None.

    `cache` is updated in place. In private mode, errors carry only the failure kind, never
    API messages or repository names, because workflow logs of a public repository are public.
    """
    values: dict = dict.fromkeys(METRICS)

    def describe(err: GitHubError) -> str:
        return err.kind if include_private else f"{err.kind}: {err}"

    try:
        user = client.graphql(USER_QUERY, {"login": login}).get("user")
        if not isinstance(user, dict):
            raise GitHubError(f"user {login} not found", kind="graphql")
        user_id, created_at, followers = user["id"], user["createdAt"], user["followers"]["totalCount"]
    except GitHubError as err:
        return CollectResult(values, errors=[f"user query failed: {describe(err)}"], user_failed=True)
    except (KeyError, TypeError):
        return CollectResult(values, errors=["user query failed: malformed"], user_failed=True)
    values.update(created_at=created_at, followers=followers)

    scope = {"version": CACHE_VERSION, "user_id": user_id, "include_private": include_private}
    if any(cache.get(key) != value for key, value in scope.items()) or not isinstance(cache.get("repos"), dict):
        cache.clear()
        cache.update(scope, repos={})
    cached_repos = {repo_id: entry for repo_id, entry in cache["repos"].items() if _valid_entry(entry)}

    privacy = None if include_private else "PUBLIC"
    try:
        affiliated = _paginate(client, AFFILIATED_QUERY, {"login": login, "privacy": privacy})
        contributed_to = _paginate(client, CONTRIBUTED_QUERY, {"login": login, "privacy": privacy})
        if not include_private:   # never trust the privacy filter alone
            affiliated = [repo for repo in affiliated if not repo["isPrivate"]]
            contributed_to = [repo for repo in contributed_to if not repo["isPrivate"]]
        for repo in affiliated + contributed_to:   # fail here, not mid-scan, on a malformed node
            repo["id"], repo["isPrivate"], repo["nameWithOwner"]
        owned = [repo for repo in affiliated if repo["owner"]["login"].lower() == login.lower()]
        stars = sum(int(repo["stargazerCount"]) for repo in owned)
    except GitHubError as err:
        return CollectResult(values, errors=[f"repository list failed: {describe(err)}"])
    except (KeyError, TypeError, ValueError):
        return CollectResult(values, errors=["repository list failed: malformed"])
    values.update(repos=len(owned), stars=stars)

    errors: list[str] = []
    scanned: dict[str, dict] = {}
    complete, used_stale_cache = True, False
    for repo in {repo["id"]: repo for repo in affiliated + contributed_to}.values():
        ref = repo.get("defaultBranchRef") or {}
        head, branch = (ref.get("target") or {}).get("oid"), ref.get("name") or ""
        if head is None:   # empty repository or no default branch
            continue
        cached = cached_repos.get(repo["id"])
        try:
            scanned[repo["id"]] = update_repo(client, repo["id"], head, branch, user_id, cached)
        except GitHubError as err:
            name = "a private repository" if repo["isPrivate"] else repo["nameWithOwner"]
            errors.append(f"commit history of {name} failed: {describe(err)}")
            if cached:
                scanned[repo["id"]] = cached   # last known data, reported as stale
                used_stale_cache = True
            else:
                complete = False
    cache["repos"] = scanned   # repositories that disappeared or became invisible drop out

    stale: set[str] = set()
    if complete:
        commits: dict[str, tuple[int, int]] = {}
        with_commits: set[str] = set()
        for repo_id, entry in scanned.items():
            for oid, (additions, deletions, is_merge) in entry["commits"].items():
                if not is_merge:
                    commits[oid] = (additions, deletions)
                    with_commits.add(repo_id)
        values.update(commits=len(commits),
                      additions=sum(additions for additions, _ in commits.values()),
                      deletions=sum(deletions for _, deletions in commits.values()),
                      contributed=len(with_commits | {repo["id"] for repo in contributed_to}))
        if used_stale_cache:
            stale = set(COMMIT_METRICS)
    return CollectResult(values, stale=stale, errors=errors)
