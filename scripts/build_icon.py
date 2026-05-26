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

from slumbr.theme import VIOLET_PRIMARY

ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = ROOT / "slumbr" / "assets" / "icon.ico"

SIZES = [16, 24, 32, 48, 64, 128, 256]


def _hex_to_rgb(s: str) -> tuple[int, int, int]:
    s = s.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def _render(size: int) -> Image.Image:
    """A clean accent dot on a fully transparent canvas — no plate.

    The earlier design painted a near-black rounded-square plate behind the
    dot, which read as "a square around the circle" in the taskbar / alt-tab.
    Drawing only circles on transparency means the corners stay cut out, so
    the shell icon matches the tray dot exactly. Same geometry as
    ``tray._icon_image``: a faint full-canvas halo + a bold inner dot.
    """
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    accent = _hex_to_rgb(VIOLET_PRIMARY) + (255,)
    halo = _hex_to_rgb(VIOLET_PRIMARY) + (70,)
    # Soft halo fills the canvas as a circle — corners stay transparent.
    d.ellipse((0, 0, size - 1, size - 1), fill=halo)
    # Bold inner dot.
    inset = max(1, round(size * 0.14))
    d.ellipse((inset, inset, size - 1 - inset, size - 1 - inset), fill=accent)
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
