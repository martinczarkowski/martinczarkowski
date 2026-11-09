"""Convert assets/portrait.jpg into the ASCII portrait used by both SVG themes.

Run locally whenever the source photo or the [portrait] settings in profile.toml change:

    python portrait.py

Writes assets/portrait.txt. Dense characters stand for dark areas of the photo (hair, beard,
jacket), so the head keeps its shape in both themes; on the dark card the same drawing reads
as light-on-dark. Requires Pillow (requirements.txt); the daily workflow only reads the
generated text file.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

ROOT = Path(__file__).resolve().parent

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


def char_for(ink: float) -> str:
    """Return the ramp character whose coverage is closest to ink (0..1)."""
    target = max(0.0, min(1.0, ink)) * MAX_DENSITY
    return min(RAMP, key=lambda item: abs(item[1] - target))[0]


def subject_mask(size: tuple[int, int], cfg: dict, with_body: bool = True) -> Image.Image:
    """Mask of the head (and shoulders); everything outside renders as blank space."""
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


def render(cfg: dict) -> list[str]:
    photo = Image.open(ROOT / cfg["source"]).convert("L")
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


def main() -> None:
    cfg = tomllib.loads((ROOT / "profile.toml").read_text(encoding="utf-8"))["portrait"]
    lines = render(cfg)
    target = ROOT / "assets" / "portrait.txt"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {target.relative_to(ROOT)} ({len(lines)} lines)")


if __name__ == "__main__":
    main()
