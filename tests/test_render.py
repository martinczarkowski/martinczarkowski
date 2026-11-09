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
HUGE = {"created_at": "2019-12-13T13:46:19Z", "followers": 1_234_567, "repos": 123_456, "stars": 9_876_543,
        "contributed": 654_321, "commits": 12_345_678, "additions": 987_654_321, "deletions": 123_456_789}
PORTRAIT = ["@@ &", "<&>"] + [""] * 23
TODAY = date(2026, 11, 9)
SVG_NS = "{http://www.w3.org/2000/svg}"

# Widest advance among the monospace fonts GitHub viewers may end up with at 16px
# (Consolas with size-adjust 109%: 9.59px, DejaVu Sans Mono / Menlo: 9.63px).
MAX_ADVANCE = 9.7


def text_of(line):
    return "".join(text for text, _ in line)


class AccountAgeTests(unittest.TestCase):
    def test_account_age_dayBeforeAnniversary_borrowsMonthAndYear(self):
        self.assertEqual(render.account_age(date(2019, 12, 13), date(2026, 11, 9)), "6 years, 10 months, 27 days")

    def test_account_age_singleUnits_usesSingular(self):
        self.assertEqual(render.account_age(date(2025, 1, 1), date(2026, 2, 2)), "1 year, 1 month, 1 day")


class LayoutTests(unittest.TestCase):
    def test_info_lines_configuredProfile_everyLineFitsWidth(self):
        for line in filter(None, render.info_lines(PROFILE, STATS, TODAY)):
            self.assertLessEqual(len(text_of(line)), render.WIDTH, text_of(line))

    def test_info_lines_keyValueRows_padToExactWidth(self):
        rows = [text_of(line) for line in filter(None, render.info_lines(PROFILE, STATS, TODAY)) if len(line) > 1]
        self.assertTrue(rows)
        self.assertTrue(all(len(row) == render.WIDTH for row in rows), rows)

    def test_info_lines_configuredProfile_fillsReferenceHeight(self):
        self.assertEqual(len(render.info_lines(PROFILE, STATS, TODAY)), render.MIN_ROWS)

    def test_info_lines_valueTooLong_raisesLayoutErrorNamingTheKey(self):
        profile = {**PROFILE, "info": [[["Host", "x" * 60]]]}
        with self.assertRaisesRegex(render.LayoutError, "Host"):
            render.info_lines(profile, STATS, TODAY)

    def test_info_lines_headerTooLong_raisesLayoutError(self):
        with self.assertRaises(render.LayoutError):
            render.info_lines({**PROFILE, "header": "h" * 58}, STATS, TODAY)

    def test_stats_lines_missingMetric_showsNotAvailableInsteadOfZero(self):
        lines = [text_of(line) for line in render.stats_lines({"repos": 7})]
        self.assertIn("{Contributed: n/a}", lines[0])
        self.assertTrue(lines[2].endswith("n/a"))
        self.assertNotIn(" 0 ", " ".join(lines))

    def test_stats_lines_lineCount_showsNetAdditionsAndDeletions(self):
        loc = text_of(render.stats_lines(STATS)[2])
        self.assertTrue(loc.endswith("98,765 ( 123,456++, 24,691-- )"), loc)

    def test_stats_lines_moreDeletionsThanAdditions_showsNegativeNet(self):
        loc = text_of(render.stats_lines({**STATS, "additions": 10, "deletions": 1_500})[2])
        self.assertIn("-1,490 ( 10++, 1,500-- )", loc)

    def test_stats_lines_hugeNumbers_fitWidthUsingCompactNotation(self):
        lines = [text_of(line) for line in render.stats_lines(HUGE)]
        for line in lines:
            self.assertLessEqual(len(line), render.WIDTH, line)
        self.assertIn("987.7M++", lines[2])

    def test_stats_lines_numbersThatFit_keepFullPrecision(self):
        lines = [text_of(line) for line in render.stats_lines({**STATS, "commits": 1_234_567})]
        self.assertIn("1,234,567", lines[1])


class SvgTests(unittest.TestCase):
    def setUp(self):
        self.svgs = render.render_all(PROFILE, PORTRAIT, STATS, TODAY)

    def test_render_all_bothThemes_produceWellFormedSvgOfReferenceSize(self):
        self.assertEqual(set(self.svgs), {"light", "dark"})
        for svg in self.svgs.values():
            root = ET.fromstring(svg.encode("utf-8"))
            self.assertEqual((root.get("width"), root.get("height")), ("985px", "530px"))

    def test_render_all_themes_useTheirOwnPalette(self):
        self.assertIn('fill="#f6f8fa"', self.svgs["light"])
        self.assertIn('fill="#161b22"', self.svgs["dark"])
        self.assertNotIn("#161b22", self.svgs["light"])

    def test_render_all_specialCharacters_escapedInPortraitAndValues(self):
        profile = {**PROFILE, "info": [[["R&D", "<SQL> & \"T-SQL\""]]]}
        for svg in render.render_all(profile, PORTRAIT, STATS, TODAY).values():
            root = ET.fromstring(svg.encode("utf-8"))
            text = "".join(root.itertext())
            self.assertIn("<SQL> & \"T-SQL\"", text)
            self.assertIn("<&>", text)

    def test_render_all_portraitAndInfo_startAtReferencePositions(self):
        root = ET.fromstring(self.svgs["light"].encode("utf-8"))
        ascii_text, info_text = root.findall(f"{SVG_NS}text")
        self.assertEqual({span.get("x") for span in ascii_text.findall(f"{SVG_NS}tspan")}, {str(render.ASCII_X)})
        self.assertEqual({span.get("x") for span in info_text.findall(f"{SVG_NS}tspan") if span.get("x")},
                         {str(render.INFO_X)})

    def test_render_all_portraitTooWide_raisesLayoutError(self):
        with self.assertRaises(render.LayoutError):
            render.render_all(PROFILE, ["@" * (render.MAX_PORTRAIT_COLUMNS + 1)], STATS, TODAY)

    def test_geometry_widestFallbackFont_keepsTextInsideCard(self):
        self.assertLessEqual(render.ASCII_X + render.MAX_PORTRAIT_COLUMNS * MAX_ADVANCE, render.INFO_X - 5)
        self.assertLessEqual(render.INFO_X + render.WIDTH * MAX_ADVANCE, render.SVG_WIDTH - 10)


if __name__ == "__main__":
    unittest.main()
