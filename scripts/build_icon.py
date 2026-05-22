"""Render slumbr/assets/icon.ico from the brand palette.

Run once (or whenever the palette changes):
    .\\.venv\\Scripts\\python.exe scripts\\build_icon.py

Generates a multi-resolution .ico at the standard Windows sizes so the
shell can pick the sharpest variant for taskbar, alt-tab, and shortcut
contexts. The design is the same violet dot the tray uses, kept simple
because at 16×16 anything more complicated mushes.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from slumbr.theme import BG_DARK, VIOLET_PRIMARY

ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = ROOT / "slumbr" / "assets" / "icon.ico"

SIZES = [16, 24, 32, 48, 64, 128, 256]


def _hex_to_rgb(s: str) -> tuple[int, int, int]:
    s = s.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def _render(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    bg = _hex_to_rgb(BG_DARK) + (255,)
    accent = _hex_to_rgb(VIOLET_PRIMARY) + (255,)
    halo = _hex_to_rgb(VIOLET_PRIMARY) + (90,)
    # Rounded-square plate sized to the full canvas — at 16×16, draw a
    # circle instead, the corner radius collapses otherwise.
    if size <= 24:
        d.ellipse((0, 0, size - 1, size - 1), fill=bg)
        margin = max(2, size // 5)
        d.ellipse(
            (margin, margin, size - 1 - margin, size - 1 - margin), fill=accent
        )
        return img
    radius = max(4, size // 6)
    d.rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=radius, fill=bg
    )
    # Halo
    halo_margin = size // 5
    d.ellipse(
        (halo_margin, halo_margin, size - 1 - halo_margin, size - 1 - halo_margin),
        fill=halo,
    )
    # Inner dot
    dot_margin = size // 4
    d.ellipse(
        (dot_margin, dot_margin, size - 1 - dot_margin, size - 1 - dot_margin),
        fill=accent,
    )
    return img


def main() -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    images = [_render(s) for s in SIZES]
    # Pillow's ICO encoder takes the largest image and writes the other
    # sizes as alternates when `sizes=` is provided. Pass the 256px as
    # the base, then let it downscale + embed the others.
    base = max(images, key=lambda im: im.size[0])
    base.save(
        OUT_PATH,
        format="ICO",
        sizes=[(s, s) for s in SIZES],
    )
    print(f"wrote {OUT_PATH} with sizes {SIZES}")


if __name__ == "__main__":
    main()
