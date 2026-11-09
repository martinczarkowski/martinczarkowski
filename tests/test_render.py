import sys
import tomllib
import unittest
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import render  # noqa: E402

PROFILE = tomllib.loads((ROOT / "profile.toml").read_text(encoding="utf-8"))
STATS = {"created_at": "2019-12-13T13:46:19Z", "followers": 3, "repos": 7, "stars": 0, "contributed": 5,
         "commits": 1234, "additions": 123456, "deletions": 24691}
PORTRAIT = ["@@ &", "<&>"] + [""] * 23


def text_of(line):
    return "".join(text for text, _ in line)


class AccountAgeTests(unittest.TestCase):
    def test_account_age_dayBeforeAnniversary_borrowsMonthAndYear(self):
        self.assertEqual(render.account_age(date(2019, 12, 13), date(2026, 11, 9)), "6 years, 10 months, 27 days")

    def test_account_age_singleUnits_usesSingular(self):
        self.assertEqual(render.account_age(date(2025, 1, 1), date(2026, 2, 2)), "1 year, 1 month, 1 day")


class LayoutTests(unittest.TestCase):
    def test_info_lines_configuredProfile_everyLineFitsWidth(self):
        lines = render.info_lines(PROFILE, STATS, date(2026, 11, 9))
        for line in filter(None, lines):
            self.assertLessEqual(len(text_of(line)), render.WIDTH, text_of(line))

    def test_info_lines_keyValueRows_padToExactWidth(self):
        lines = render.info_lines(PROFILE, STATS, date(2026, 11, 9))
        rows = [text_of(line) for line in filter(None, lines) if len(line) > 1]
        self.assertTrue(rows)
        self.assertTrue(all(len(row) == render.WIDTH for row in rows), rows)

    def test_stats_lines_missingMetric_showsNotAvailableInsteadOfZero(self):
        lines = [text_of(line) for line in render.stats_lines({"repos": 7})]
        self.assertIn("{Contributed: n/a}", lines[0])
        self.assertTrue(lines[2].endswith("n/a"))
        self.assertNotIn(" 0 ", " ".join(lines))

    def test_stats_lines_lineCount_showsNetAdditionsAndDeletions(self):
        loc = text_of(render.stats_lines(STATS)[2])
        self.assertTrue(loc.endswith("98,765 ( 123,456++, 24,691-- )"), loc)


class SvgTests(unittest.TestCase):
    def test_render_all_bothThemes_produceWellFormedSvgWithEscapedPortrait(self):
        svgs = render.render_all(PROFILE, PORTRAIT, STATS, date(2026, 11, 9))
        self.assertEqual(set(svgs), {"light", "dark"})
        for svg in svgs.values():
            root = ET.fromstring(svg.encode("utf-8"))
            self.assertEqual(root.get("width"), "985px")
            self.assertEqual(root.get("height"), "530px")
            self.assertIn("Database &amp; Backend Developer", svg)


if __name__ == "__main__":
    unittest.main()
