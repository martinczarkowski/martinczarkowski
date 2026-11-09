"""Daily profile update: refresh GitHub statistics and re-render light_mode.svg and dark_mode.svg.

    GH_TOKEN=... python today.py                   # public statistics (default)
    GH_TOKEN=... python today.py --include-private # also private repositories the token can read
    python today.py --offline                      # re-render from stats.json, no API calls

stats.json records `status` (complete / partial / failed) and `stale`, the metrics that show a
previous or cached value because they could not be refreshed. A metric that never loaded stays
null and renders as n/a, never as a made-up zero.

Exit codes: 0 complete, 1 partial or failed refresh (files are still written with the best
known values), 2 configuration error (no token, content does not fit the card; nothing written).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path

import github_stats
import render

ROOT = Path(__file__).resolve().parent


def merge_stats(previous: dict, result: github_stats.CollectResult, scope: str) -> dict:
    """Fresh values where available, otherwise the previous value of the same scope, marked stale."""
    same_scope = previous.get("scope") == scope
    stale = set(result.stale)
    stats: dict = {"scope": scope}
    for key, value in result.values.items():
        if value is None and same_scope and previous.get(key) is not None:
            value = previous[key]
            stale.add(key)
        stats[key] = value
    stats["status"] = result.status
    stats["stale"] = sorted(stale)
    return stats


def load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        print(f"::warning::{path.name} is unreadable, starting without previous values")
        return {}
    return data if isinstance(data, dict) else {}


def write_atomic(path: Path, content: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def write_summary(stats: dict, errors: list[str]) -> None:
    """GitHub Actions job summary; errors are already free of private repository details."""
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if not target:
        return
    lines = [f"### Profile statistics: {stats.get('status', 'offline')}", ""]
    if stats.get("stale"):
        lines.append(f"Previous or cached values kept for: {', '.join(stats['stale'])}")
    lines += [f"- {error}" for error in errors]
    with open(target, "a", encoding="utf-8") as summary:
        summary.write("\n".join(lines) + "\n")


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--offline", action="store_true", help="render from stats.json without calling the API")
    parser.add_argument("--include-private", action="store_true",
                        default=os.environ.get("INCLUDE_PRIVATE", "").lower() == "true",
                        help="include private repositories (or set INCLUDE_PRIVATE=true)")
    args = parser.parse_args(argv)

    profile = tomllib.loads((root / "profile.toml").read_text(encoding="utf-8"))
    portrait = (root / "assets" / "portrait.txt").read_text(encoding="utf-8").splitlines()
    stats = load_json(root / "stats.json")
    errors: list[str] = []

    if not args.offline:
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if not token:
            print("error: set GH_TOKEN (or run with --offline)", file=sys.stderr)
            return 2
        cache_file = root / "cache" / "stats_cache.json"
        cache = github_stats.load_cache(cache_file)
        result = github_stats.collect(github_stats.GitHubClient(token), profile["login"], args.include_private, cache)
        github_stats.save_cache(cache_file, cache)
        errors = result.errors
        stats = merge_stats(stats, result, "all" if args.include_private else "public")

    try:
        svgs = render.render_all(profile, portrait, stats, datetime.now(UTC).date())
    except render.LayoutError as err:
        print(f"::error::{err}")
        return 2

    if not args.offline:
        write_atomic(root / "stats.json", json.dumps(stats, indent=2, sort_keys=True) + "\n")
    for theme, svg in svgs.items():
        write_atomic(root / f"{theme}_mode.svg", svg)

    for error in errors:
        print(f"::warning::{error}")
    print("stats:", json.dumps(stats, sort_keys=True))
    write_summary(stats, errors)
    if not args.offline and stats["status"] != "complete":
        stale = ", ".join(stats["stale"]) or "none"
        print(f"::error::statistics {stats['status']}: previous or cached values kept for: {stale}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
