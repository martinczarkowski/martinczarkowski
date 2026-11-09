# Profile README: setup and maintenance

The profile is a single SVG card in two themes, modelled on
[Andrew6rant's profile](https://github.com/Andrew6rant/Andrew6rant). `README.md` only switches
between `light_mode.svg` and `dark_mode.svg`.

| File | Purpose |
|---|---|
| `profile.toml` | Card content and portrait settings |
| `portrait.py` | Photo → `assets/portrait.txt`, shared by both themes (Pillow, run locally) |
| `github_stats.py` | GitHub GraphQL queries, per-repository commit cache |
| `render.py` | SVG layout (985×530, Consolas, 60-character info lines) |
| `today.py` | Entry point: refresh the statistics, keep last good values, render both SVGs |
| `stats.json` | Last good aggregate statistics (no repository names) |
| `.github/workflows/build.yml` | Daily update, manual run, and re-render after source changes |

## Local use

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Linux/macOS: .venv/bin/python

python portrait.py                    # after replacing assets/portrait.jpg or tuning [portrait]
python today.py --offline             # re-render from stats.json, no API calls
GH_TOKEN=$(gh auth token) python today.py   # real public statistics
python -m unittest discover -s tests
```

To use a different photo, replace `assets/portrait.jpg`. Then adjust `head`, `body` and the crop
values in `profile.toml` so the mask covers your head and shoulders, and run `portrait.py`.
The mask drops the background and keeps the portrait readable.

## Workflow and tokens

The workflow runs daily at 04:17 UTC. It also runs on demand (*Actions → Update profile README →
Run workflow*) and after a push to `master` that changes a source file. It commits the
regenerated `light_mode.svg`, `dark_mode.svg` and `stats.json` only when they changed. That still
means one commit a day, because the account age changes every day. Commits made with
`GITHUB_TOKEN` do not trigger workflows, and generated files are excluded from the push
trigger, so the workflow cannot loop.

**Default: no secret needed.** The built-in `GITHUB_TOKEN` reads public data, which is enough for
the default public-only statistics.

**Optional: include private repositories.**
1. Create a fine-grained personal access token (*Settings → Developer settings → Fine-grained
   tokens*). Set the resource owner to `martinczarkowski` and repository access to *All
   repositories*. Grant Repository permissions *Contents: Read-only* and *Metadata: Read-only*.
   Nothing else is needed, and the token cannot write anything. Set an expiry and renew it.
2. Add it as an Actions secret `PROFILE_STATS_TOKEN` (*repository Settings → Secrets and
   variables → Actions → Secrets*).
3. Add an Actions variable `INCLUDE_PRIVATE` with the value `true` (same page, *Variables* tab).

The SVG and `stats.json` contain only aggregate numbers. Error messages never name a private
repository, because Actions logs of a public repository are public. The per-repository commit
cache is stored with `actions/cache`, not committed. A fine-grained token covers only
repositories owned by its resource owner. Private repositories of other users or organisations
where you collaborate are therefore not counted.

## What the numbers mean

- **Repos**: repositories owned by the account (public only, unless private mode is on).
- **Contributed**: repositories with at least one of your commits on the default branch, plus
  GitHub's `repositoriesContributedTo` list. GitHub builds that list from repositories you
  *recently* committed to, opened issues or pull requests in, reviewed, or created. GitHub does
  not document the exact time window, so older contributions to repositories you cannot scan may
  be missing.
- **Stars**: stargazers summed over owned repositories.
- **Commits**: non-merge commits on the default branch whose author is linked to the account (by a
  verified e-mail), across owned, collaborator, organisation-member and contributed-to
  repositories. A commit present in several repositories (forks) is counted once. This is not the
  contribution-calendar total. Commits on other branches, co-authored commits and commits made
  with an unlinked e-mail are not counted.
- **Net Lines Committed**: additions minus deletions over those same commits, with additions
  (`++`) and deletions (`--`). This is churn, not the number of lines that currently exist. A
  file added and later deleted adds to both sides, and generated or vendored files count like any
  other change.
- **GitHub.Uptime**: age of the GitHub account, from the API's `createdAt`.

If a metric cannot be loaded (API error, rate limit), the card keeps its last good value from
`stats.json` and shows `n/a` if there is none. It never shows a made-up zero. The run is then
marked as failed so the problem is visible. Repositories that were deleted or that you lost
access to drop out of the counts at the next run.

## Publishing

Review the diff, commit and push to `master`. Then run the workflow once by hand (*Run
workflow*) to confirm that `GITHUB_TOKEN` can read the statistics in Actions.
