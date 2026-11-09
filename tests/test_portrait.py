import importlib.util
import shutil
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import portrait  # noqa: E402
import render  # noqa: E402

HAS_PILLOW = importlib.util.find_spec("PIL") is not None
CFG = portrait.load_config()
COMMITTED = (ROOT / "assets" / "portrait.txt").read_text(encoding="utf-8").splitlines()


class ConfigTests(unittest.TestCase):
    def test_validate_configuredPortrait_passes(self):
        portrait.validate(CFG)

    def test_validate_cropTopBelowBottom_raises(self):
        with self.assertRaises(ValueError):
            portrait.validate({**CFG, "crop_top": 0.9, "crop_bottom": 0.5})

    def test_validate_headOutsidePhoto_raises(self):
        with self.assertRaises(ValueError):
            portrait.validate({**CFG, "head": [1.2, 0.4, 0.2, 0.3]})

    def test_validate_tooManyColumns_raises(self):
        with self.assertRaises(ValueError):
            portrait.validate({**CFG, "columns": render.MAX_PORTRAIT_COLUMNS + 1})

    def test_crop_box_configuredPortrait_staysInsidePhotoWithGridAspect(self):
        left, top, right, bottom = portrait.crop_box((460, 460), CFG)
        self.assertTrue(0 <= left < right <= 460 and 0 <= top < bottom <= 460)
        grid_aspect = CFG["columns"] * portrait.CELL_WIDTH / (CFG["rows"] * portrait.CELL_HEIGHT)
        self.assertAlmostEqual((right - left) / (bottom - top), grid_aspect, delta=0.01)


class FingerprintTests(unittest.TestCase):
    def test_fingerprint_committedPortrait_matchesItsInputs(self):
        stored = (ROOT / "assets" / "portrait.sha256").read_text(encoding="utf-8").strip()
        self.assertEqual(portrait.fingerprint(CFG), stored,
                         "assets/portrait.txt is out of date: run `python portrait.py`")

    def test_fingerprint_windowsLineEndings_doNotChangeIt(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("portrait.py", "requirements.txt"):
                text = (ROOT / name).read_text(encoding="utf-8").replace("\r\n", "\n")
                (root / name).write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
            (root / "assets").mkdir()
            shutil.copy(ROOT / CFG["source"], root / CFG["source"])
            self.assertEqual(portrait.fingerprint(CFG, root), portrait.fingerprint(CFG))

    def test_fingerprint_settingChanged_differs(self):
        self.assertNotEqual(portrait.fingerprint({**CFG, "sharpen": CFG["sharpen"] + 1}), portrait.fingerprint(CFG))


@unittest.skipUnless(HAS_PILLOW, "Pillow not installed (pip install -r requirements.txt)")
class RenderTests(unittest.TestCase):
    def setUp(self):
        self.lines = portrait.render(CFG)

    def test_render_sameInputs_isDeterministic(self):
        self.assertEqual(portrait.render(CFG), self.lines)

    def test_render_configuredGrid_hasExpectedDimensions(self):
        self.assertEqual(len(self.lines), CFG["rows"])
        self.assertLessEqual(max(map(len, self.lines)), CFG["columns"])

    def test_render_committedPortrait_matchesFreshConversion(self):
        # Allow a few boundary characters to differ between Pillow builds (Windows vs Linux wheels).
        fresh = [line.ljust(CFG["columns"]) for line in self.lines]
        committed = [line.ljust(CFG["columns"]) for line in COMMITTED]
        same = sum(a == b for fl, cl in zip(fresh, committed) for a, b in zip(fl, cl))
        self.assertGreaterEqual(same / (CFG["rows"] * CFG["columns"]), 0.97)

    def test_render_backgroundOutsideMask_staysEmpty(self):
        self.assertTrue(self.lines[0].startswith(" " * 5), self.lines[0])


if __name__ == "__main__":
    unittest.main()
