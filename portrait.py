"""Convert assets/portrait.jpg into the ASCII portrait used by both SVG themes.

    python portrait.py          # regenerate assets/portrait.txt and assets/portrait.sha256
    python portrait.py --check  # exit 1 if portrait.txt is out of date (standard library only)

Dense characters stand for dark areas of the photo (hair, beard, jacket), so the head keeps
its shape in both themes; on the dark card the same drawing reads as light-on-dark.

assets/portrait.sha256 fingerprints every input of the conversion: the photo, the [portrait]
settings, this script and the pinned Pillow version. The workflow installs Pillow and
regenerates the portrait only when the fingerprint no longer matches.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tomllib
from pathlib import Path

from render import MAX_PORTRAIT_COLUMNS

ROOT = Path(__file__).resolve().parent
OUTPUT = Path("assets") / "portrait.txt"
FINGERPRINT = Path("assets") / "portrait.sha256"

# Character advance and line height of the SVG text (Consolas 16px with size-adjust 109%).
CELL_WIDTH = 9.59
CELL_HEIGHT = 20

# Glyph ink coverage measured in Consolas, from empty to densest.
RAMP = [
    (" ", 0.0000), ("`", 0.0139), (".", 0.0216), ("'", 0.0235), (":", 0.0365), (",", 0.0415),
    (";", 0.0598), ("!", 0.0609), ("L", 0.0838), ("|", 0.0855), ("T", 0.0913), ("l", 0.0994),
    ("]", 0.1021), ("F", 0.1040), ("j", 0.1126), ("k", 0.1218), ("w", 0.1298), ("m", 0.1403),
    ("%", 0.1624), ("$", 0.1624), ("M", 0.1672), ("g", 0.1775), ("&", 0.1809), ("@", 0.2156),
]
MAX_DENSITY = RAMP[-1][1]


def load_config(root: Path = ROOT) -> dict:
    return tomllib.loads((root / "profile.toml").read_text(encoding="utf-8"))["portrait"]


def validate(cfg: dict) -> None:
    """Raise ValueError for settings that would distort the face or overflow the card."""
    def fraction(value) -> bool:
        return isinstance(value, (int, float)) and 0 <= value <= 1

    if not 0 < cfg["columns"] <= MAX_PORTRAIT_COLUMNS or cfg["rows"] <= 0:
        raise ValueError(f"columns must be 1..{MAX_PORTRAIT_COLUMNS} and rows positive")
    if not (fraction(cfg["crop_top"]) and fraction(cfg["crop_bottom"]) and cfg["crop_top"] < cfg["crop_bottom"]):
        raise ValueError("crop_top and crop_bottom must be fractions with crop_top < crop_bottom")
    if not (fraction(cfg["center_x"]) and len(cfg["head"]) == 4 and all(map(fraction, cfg["head"]))):
        raise ValueError("center_x and head must be fractions of the photo")
    if len(cfg["body"]) < 3 or not all(len(point) == 2 and all(map(fraction, point)) for point in cfg["body"]):
        raise ValueError("body must be a polygon of at least 3 [x, y] fractions")


def pillow_version(root: Path = ROOT) -> str:
    match = re.search(r"(?im)^pillow==([^\s\\]+)", (root / "requirements.txt").read_text(encoding="utf-8"))
    return match.group(1) if match else "unpinned"


def fingerprint(cfg: dict, root: Path = ROOT) -> str:
    """Hash of everything that determines the portrait; line endings do not matter."""
    digest = hashlib.sha256()
    digest.update((root / cfg["source"]).read_bytes())
    digest.update(json.dumps(cfg, sort_keys=True).encode())
    digest.update((root / "portrait.py").read_bytes().replace(b"\r\n", b"\n"))
    digest.update(pillow_version(root).encode())
    return digest.hexdigest()


def char_for(ink: float) -> str:
    """Return the ramp character whose coverage is closest to ink (0..1)."""
    target = max(0.0, min(1.0, ink)) * MAX_DENSITY
    return min(RAMP, key=lambda item: abs(item[1] - target))[0]


def subject_mask(size: tuple[int, int], cfg: dict, with_body: bool = True):
    """Mask of the head (and shoulders); everything outside renders as blank space."""
    from PIL import Image, ImageDraw

    width, height = size
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    cx, cy, rx, ry = cfg["head"]
    draw.ellipse([(cx - rx) * width, (cy - ry) * height, (cx + rx) * width, (cy + ry) * height], fill=255)
    if with_body:
        draw.polygon([(x * width, y * height) for x, y in cfg["body"]], fill=255)
    return mask


def crop_box(size: tuple[int, int], cfg: dict) -> tuple[int, int, int, int]:
    """Crop with the aspect ratio of the ASCII grid so characters are not distorted."""
    width, height = size
    top, bottom = cfg["crop_top"] * height, cfg["crop_bottom"] * height
    crop_width = (bottom - top) * (cfg["columns"] * CELL_WIDTH) / (cfg["rows"] * CELL_HEIGHT)
    left = cfg["center_x"] * width - crop_width / 2
    return round(left), round(top), round(left + crop_width), round(bottom)


def render(cfg: dict, root: Path = ROOT) -> list[str]:
    from PIL import Image, ImageFilter, ImageOps

    validate(cfg)
    photo = Image.open(root / cfg["source"]).convert("L")
    # Equalise on the face only: the dark background and jacket must not eat the tonal range.
    photo = ImageOps.equalize(photo, mask=subject_mask(photo.size, cfg, with_body=False))
    mask = subject_mask(photo.size, cfg).filter(ImageFilter.GaussianBlur(cfg["mask_blur"] * photo.width))
    box = crop_box(photo.size, cfg)
    photo, mask = photo.crop(box), mask.crop(box)
    photo = photo.filter(ImageFilter.UnsharpMask(radius=3, percent=cfg["sharpen"], threshold=2))
    edges = photo.filter(ImageFilter.FIND_EDGES)

    grid = (cfg["columns"], cfg["rows"])
    photo, edges, mask = (image.resize(grid, Image.Resampling.BOX) for image in (photo, edges, mask))

    lines = []
    for row in range(cfg["rows"]):
        chars = []
        for col in range(cfg["columns"]):
            coverage = mask.getpixel((col, row)) / 255
            if coverage < 0.05:   # outside the subject: keep the background empty
                chars.append(" ")
                continue
            darkness = 1 - photo.getpixel((col, row)) / 255
            edge = edges.getpixel((col, row)) / 255
            chars.append(char_for((darkness ** cfg["gamma"] + cfg["edge_weight"] * edge) * coverage))
        lines.append("".join(chars).rstrip())
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="only report whether portrait.txt is up to date")
    args = parser.parse_args(argv)

    cfg = load_config()
    validate(cfg)
    expected = fingerprint(cfg)
    stored = (ROOT / FINGERPRINT).read_text(encoding="utf-8").strip() if (ROOT / FINGERPRINT).exists() else ""
    if args.check:
        print("portrait is up to date" if stored == expected else "portrait is out of date")
        return 0 if stored == expected else 1

    lines = render(cfg)
    (ROOT / OUTPUT).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    (ROOT / FINGERPRINT).write_text(expected + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUTPUT.as_posix()} ({len(lines)} lines) and {FINGERPRINT.as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
