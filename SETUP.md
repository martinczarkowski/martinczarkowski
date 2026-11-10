# Profile README: setup and maintenance

The profile is a single SVG card in two themes, modelled on
[Andrew6rant's profile](https://github.com/Andrew6rant/Andrew6rant). `README.md` only switches
between `light_mode.svg` and `dark_mode.svg`.

| File | Purpose |
|---|---|
| `profile.toml` | Card content and portrait settings |
| `portrait.py` | Photo → `assets/portrait.txt` (shared by both themes) + `assets/portrait.sha256` |
| `github_stats.py` | GitHub GraphQL client, statistics, per-repository commit cache |
| `render.py` | SVG layout (985×530, Consolas, 60-character info lines, 25 rows) |
| `today.py` | Entry point: refresh statistics, keep last good values, render both SVGs |
| `stats.json` | Last published statistics, their `status` and `stale` metrics (no repository names) |
| `.github/workflows/build.yml` | Daily update, manual run, and re-render after source changes |
| `.github/workflows/test.yml` | Tests for pull requests (no secrets, read-only) |

## Local use

Requires Python 3.11+ (CI uses 3.14). Only `portrait.py` needs a dependency (Pillow).

```bash
python -m venv .venv
.venv/Scripts/python -m pip install --require-hashes --only-binary :all: -r requirements.txt  # Linux/macOS: .venv/bin/python

python portrait.py --check            # is assets/portrait.txt up to date? (no Pillow needed)
python portrait.py                    # regenerate it after changing the photo or [portrait]
python today.py --offline             # re-render the SVGs from stats.json, no API calls
GH_TOKEN=$(gh auth token) python today.py   # refresh real public statistics
python -m unittest discover -s tests -v     # Pillow-dependent tests are skipped without Pillow
```

`requirements.txt` pins Pillow by version and SHA-256 hash. The hashes cover the CPython 3.14
wheels for Linux x86_64, Windows x64 and macOS arm64. On another platform or Python version, add
the matching hash from PyPI.

### Portrait

To use a different photo, replace `assets/portrait.jpg`. Then adjust the crop, `head` and `body`
values in `profile.toml` so the mask covers your head and shoulders, run `portrait.py`, and commit
`assets/portrait.txt` and `assets/portrait.sha256`. The conversion is deterministic for a given
photo, settings and Pillow version. Dense characters stand for dark areas, so hair, beard and
jacket keep the head's outline in both themes.

`assets/portrait.sha256` fingerprints the photo, the `[portrait]` settings, `portrait.py` and the
pinned Pillow version. If you push a change to one of them without regenerating, the workflow
notices the mismatch, installs Pillow, regenerates the portrait and commits it. Otherwise it never
installs Pillow.

### Content

`profile.toml` holds the rows of the right column. Every line must fit 60 characters and the
card 25 rows. Rendering stops with a clear `LayoutError` (exit code 2, nothing written) rather
than letting text overflow. Statistics that would not fit with full thousands separators are
shown in compact notation (`987.7M`) on that line only.

## Workflow

`build.yml` runs daily at 04:17 UTC, on demand (*Actions → Update profile README → Run
workflow*), and after a push to `master` that changes a source file. It runs these steps in order:

1. Checks the portrait fingerprint and regenerates the portrait only if it is stale.
2. Runs the unit tests. If a test fails, nothing is published.
3. Restores the commit cache (public mode only).
4. Runs `today.py`.
5. Saves the cache.
6. Commits `light_mode.svg`, `dark_mode.svg`, `stats.json` and the portrait files, but only when
   they changed. The account age changes every day, so a daily commit is expected.

Guarantees:

- Commits made with `GITHUB_TOKEN` do not trigger workflows, and the generated files are not in
  the push trigger, so the workflow cannot loop.
- A concurrency group prevents two updates from racing.
- All actions are pinned to commit SHAs. Checkout does not keep the token, which reaches only the
  commit step.

`today.py` exit codes:

| Code | Meaning | Workflow |
|---|---|---|
| 0 | All statistics refreshed (`status: complete`) | success, commit if changed |
| 1 | Some statistics not refreshed (`partial`/`failed`) | SVGs and `stats.json` are committed with the best known values, the run is marked failed, and the job summary lists the stale metrics |
| 2 | Configuration error: no token, or content does not fit the card | nothing written or committed |

## Tokens and private statistics

**Default: no secret needed.** The built-in `GITHUB_TOKEN` reads public data, which is all the
default public-only statistics need.

**Optional: include private repositories.**
1. Create a fine-grained personal access token (*Settings → Developer settings → Fine-grained
   tokens*). Set the resource owner to `martinczarkowski` and repository access to *All
   repositories*. Grant Repository permissions *Contents: Read-only* and *Metadata: Read-only*,
   nothing else. Set an expiry and renew it.
2. Add it as the Actions secret `PROFILE_STATS_TOKEN`.
3. Add the Actions variable `INCLUDE_PRIVATE` with the value `true`.

What private mode does and does not expose:

- The SVG and `stats.json` contain aggregate numbers only.
- Log messages and the job summary carry only the failure kind (for example `network` or
  `rate_limited`). They never include repository names or API error text, because Actions logs of
  a public repository are public.
- The per-repository commit cache is **not** stored in the Actions cache in private mode. Caches
  of the default branch can be restored by pull-request workflows, so private commit metadata
  must not be in them. Every private-mode run therefore rescans all commit histories. That is
  slower, but well within API limits for a personal account.
- A fine-grained token covers only repositories owned by its resource owner. Private repositories
  of other users or organisations are not counted.

Residual risk: the update job's own `GITHUB_TOKEN` can write to this repository. It is passed only
to the commit step, but any code running in that job, including Pillow when the portrait is
regenerated, runs on the same machine. Pillow is installed only from hash-verified wheels.

## Cache

`cache/stats_cache.json` is gitignored. In public mode it is kept between workflow runs with
`actions/cache`. For each scanned repository it stores the default-branch name and head, plus the
account's commits on that branch (oid, additions, deletions, merge flag).

- **Unchanged head:** the repository is not queried.
- **New head:** only newer commits are fetched, until the first known one.
- **Rewritten history, renamed branch or count mismatch:** that branch is rescanned in full.
  Known limitation: a rewrite on the same branch that removes some of your commits and adds the
  same number of older ones keeps the count equal and is not detected until the next mismatch.
- **Repository deleted, made private (in public mode) or no longer accessible:** it drops out.
- **Changes in user, scope or cache format:** the whole cache resets.
- **Corrupted cache:** the whole file or the bad entry is ignored and rebuilt.
- **Failed repository with a cached entry:** the cached data is used and reported as stale.
- **Failed repository without a cached entry:** the commit metrics are unknown and the previous
  published value is kept.

## What the numbers mean

- **Repos**: repositories owned by the account, including forks (public only unless private mode
  is on).
- **Contributed**: the union, without duplicates, of two sets. The first is repositories with at
  least one of your counted commits. The second is GitHub's `repositoriesContributedTo` list:
  repositories you *recently* committed to, opened issues or pull requests in, reviewed, or
  created. GitHub does not document the exact window, so older contributions to repositories that
  are not otherwise scanned may be missing.
- **Stars**: stargazers summed over owned repositories.
- **Commits**: non-merge commits on the default branch whose author GitHub links to the account
  (by a verified e-mail). They are counted across owned, collaborator, organisation-member and
  contributed-to repositories. A commit present in several repositories (forks) is counted once.
  This is not the contribution-calendar total. Not counted:
  - commits on other branches;
  - co-authored commits;
  - commits made with an e-mail address not linked to the account.
- **Net Lines Committed**: additions minus deletions over the same commits, shown with additions
  (`++`) and deletions (`--`). This is churn, not the number of lines that exist today. A file
  added and later deleted counts on both sides, and generated or vendored files count like any
  other change.
- **GitHub.Age**: age of the GitHub account, from the API's `createdAt`.

Repositories whose owner is listed in `exclude_owners` in `profile.toml` (case-insensitive) are
left out of every metric.

A metric that cannot be refreshed keeps its previous value and is listed in `stats.json` under
`stale`. A metric that never loaded shows `n/a`. Neither is ever replaced by a zero.

## Troubleshooting a failed run

Open the run and read the job summary: it shows the status and which metrics are stale.

| Symptom | Cause | Fix |
|---|---|---|
| `user query failed: auth` | token invalid or expired | renew `PROFILE_STATS_TOKEN`, or delete it to fall back to `GITHUB_TOKEN` |
| `rate_limited` | GraphQL rate limit; requests stop for this run | wait for the next run, or re-run later |
| `network` | GitHub API outage or timeouts; the remaining requests are skipped | re-run later; the published values stay |
| `malformed` | unexpected API response | re-run; if it persists, the API changed and `github_stats.py` needs updating |
| `LayoutError` (exit 2) | a `profile.toml` value is too long | shorten the value |
| Tests failed | a code or content change broke something | fix it locally with `python -m unittest discover -s tests -v` |
| Push rejected | you pushed to `master` while the job was running | nothing to do, the next run catches up |

## Publishing changes

Work on a branch and open a pull request: `test.yml` runs the tests. After merging, `build.yml`
regenerates the SVGs.
