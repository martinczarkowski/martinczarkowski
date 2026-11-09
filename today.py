"""Daily profile update: refresh GitHub statistics and re-render light_mode.svg and dark_mode.svg.

    GH_TOKEN=... python today.py                   # public statistics (default)
    GH_TOKEN=... python today.py --include-private # also private repositories the token can read
    python today.py --offline                      # re-render from stats.json, no API calls

A metric that fails to load keeps its last good value from stats.json (never a fake zero);
the script then exits with code 1 so the workflow run is marked as failed.
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
STATS_FILE = ROOT / "stats.json"
CACHE_FILE = ROOT / "cache" / "stats_cache.json"


def merge_stats(previous: dict, fresh: dict, scope: str) -> tuple[dict, list[str]]:
    """New values where available, otherwise the previous value of the same scope."""
    same_scope = previous.get("scope") == scope
    stats, failed = {"scope": scope}, []
    for key, value in fresh.items():
        if value is None:
            failed.append(key)
            value = previous.get(key) if same_scope else None
        stats[key] = value
    return stats, failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--offline", action="store_true", help="render from stats.json without calling the API")
    parser.add_argument("--include-private", action="store_true",
                        default=os.environ.get("INCLUDE_PRIVATE", "").lower() == "true",
                        help="include private repositories (or set INCLUDE_PRIVATE=true)")
    args = parser.parse_args(argv)

    profile = tomllib.loads((ROOT / "profile.toml").read_text(encoding="utf-8"))
    previous = json.loads(STATS_FILE.read_text(encoding="utf-8")) if STATS_FILE.exists() else {}
    stats, failed = previous, []

    if not args.offline:
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if not token:
            print("error: set GH_TOKEN (or run with --offline)", file=sys.stderr)
            return 2
        cache = github_stats.load_cache(CACHE_FILE)
        fresh, errors = github_stats.collect(github_stats.GitHubClient(token), profile["login"],
                                             args.include_private, cache)
        github_stats.save_cache(CACHE_FILE, cache)
        for error in errors:
            print(f"::warning::{error}")
        stats, failed = merge_stats(previous, fresh, "all" if args.include_private else "public")
        STATS_FILE.write_text(json.dumps(stats, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")

    portrait = (ROOT / "assets" / "portrait.txt").read_text(encoding="utf-8").splitlines()
    today = datetime.now(UTC).date()
    for theme, svg in render.render_all(profile, portrait, stats, today).items():
        (ROOT / f"{theme}_mode.svg").write_text(svg, encoding="utf-8", newline="\n")

    print("stats:", json.dumps(stats, sort_keys=True))
    if failed:
        print(f"::error::not refreshed, previous values kept: {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
