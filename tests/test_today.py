import json
import os
import shutil
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import github_stats as gs  # noqa: E402
import today  # noqa: E402

VALUES = {"created_at": "2019-12-13T13:46:19Z", "followers": 3, "repos": 7, "stars": 0, "contributed": 5,
          "commits": 25, "additions": 2145, "deletions": 30}


def result(values=None, stale=(), errors=(), failed=False):
    values = dict(VALUES if values is None else values)
    return gs.CollectResult(values=values, stale=set(stale), errors=list(errors), user_failed=failed)


class MergeStatsTests(unittest.TestCase):
    def test_merge_stats_completeResult_reportsCompleteWithoutStaleMetrics(self):
        stats = today.merge_stats({}, result(), "public")
        self.assertEqual((stats["status"], stats["stale"], stats["commits"]), ("complete", [], 25))

    def test_merge_stats_failedMetric_keepsPreviousValueMarkedStale(self):
        previous = {"scope": "public", "commits": 20}
        stats = today.merge_stats(previous, result({**VALUES, "commits": None}, errors=["x"]), "public")
        self.assertEqual((stats["commits"], stats["status"]), (20, "partial"))
        self.assertIn("commits", stats["stale"])

    def test_merge_stats_staleCachedMetric_reportedAsStale(self):
        stats = today.merge_stats({}, result(stale={"commits", "additions"}, errors=["x"]), "public")
        self.assertEqual(stats["stale"], ["additions", "commits"])
        self.assertEqual(stats["status"], "partial")

    def test_merge_stats_scopeChanged_doesNotReuseOtherScopeValue(self):
        previous = {"scope": "public", "commits": 25}
        stats = today.merge_stats(previous, result({**VALUES, "commits": None}, errors=["x"]), "all")
        self.assertIsNone(stats["commits"])
        self.assertNotIn("commits", stats["stale"])

    def test_merge_stats_metricNeverLoaded_staysUnknownNotZero(self):
        stats = today.merge_stats({}, result({**VALUES, "stars": None}, errors=["x"]), "public")
        self.assertIsNone(stats["stars"])


class MainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        shutil.copy(ROOT / "profile.toml", self.root / "profile.toml")
        (self.root / "assets").mkdir()
        shutil.copy(ROOT / "assets" / "portrait.txt", self.root / "assets" / "portrait.txt")
        self.write_stats({"scope": "public", **VALUES})

    def tearDown(self):
        self.tmp.cleanup()

    def write_stats(self, stats):
        (self.root / "stats.json").write_text(json.dumps(stats), encoding="utf-8")

    def run_main(self, *args, env=None):
        with mock.patch.dict(os.environ, env or {}, clear=True), mock.patch("builtins.print"):
            return today.main(list(args), root=self.root)

    def test_main_offline_rendersBothSvgsAndSucceeds(self):
        self.assertEqual(self.run_main("--offline"), 0)
        self.assertTrue((self.root / "light_mode.svg").exists())
        self.assertTrue((self.root / "dark_mode.svg").exists())

    def test_main_offlineWithCorruptedStats_rendersNotAvailable(self):
        (self.root / "stats.json").write_text("{broken", encoding="utf-8")
        self.assertEqual(self.run_main("--offline"), 0)
        self.assertIn("n/a", (self.root / "light_mode.svg").read_text(encoding="utf-8"))

    def test_main_withoutToken_failsWithConfigurationError(self):
        self.assertEqual(self.run_main(), 2)
        self.assertFalse((self.root / "light_mode.svg").exists())

    def test_main_profileValueTooLong_failsWithoutWritingFiles(self):
        profile = (self.root / "profile.toml").read_text(encoding="utf-8")
        (self.root / "profile.toml").write_text(profile.replace('"Safetica"', '"' + "S" * 80 + '"'), encoding="utf-8")
        self.assertEqual(self.run_main("--offline"), 2)
        self.assertFalse((self.root / "light_mode.svg").exists())

    def test_main_partialRefresh_writesSvgsButExitsWithFailure(self):
        partial = result({**VALUES, "commits": None}, errors=["commit history of a private repository failed: http"])
        with mock.patch.object(today.github_stats, "collect", return_value=partial):
            code = self.run_main(env={"GH_TOKEN": "x"})
        stats = json.loads((self.root / "stats.json").read_text(encoding="utf-8"))
        self.assertEqual((code, stats["status"], stats["commits"]), (1, "partial", 25))
        self.assertTrue((self.root / "light_mode.svg").exists())

    def test_main_completeRefresh_exitsSuccessfully(self):
        with mock.patch.object(today.github_stats, "collect", return_value=result()):
            self.assertEqual(self.run_main(env={"GH_TOKEN": "x"}), 0)

    def test_main_stepSummary_reportsStatusAndStaleMetrics(self):
        summary = self.root / "summary.md"
        partial = result(stale={"commits"}, errors=["commit history of a private repository failed: http"])
        with mock.patch.object(today.github_stats, "collect", return_value=partial):
            self.run_main(env={"GH_TOKEN": "x", "GITHUB_STEP_SUMMARY": str(summary)})
        text = summary.read_text(encoding="utf-8")
        self.assertIn("partial", text)
        self.assertIn("commits", text)


if __name__ == "__main__":
    unittest.main()
