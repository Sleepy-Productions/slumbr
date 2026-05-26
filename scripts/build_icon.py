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
    """One solid accent circle on a fully transparent canvas — nothing else.

    History: the original drew a near-black rounded-square plate behind the
    dot (read as "a square around the circle"). The first fix swapped that for
    a translucent halo + inner dot — but over a dark taskbar the halo renders
    as a dim ring that *still* frames the dot like a backing plate. So: no
    plate, no halo. Just the oval, cut out clean, corners fully transparent.
    """
    # PIL's ellipse draws a hard, aliased rim. Supersample 4× and downscale
    # with LANCZOS so the circle's edge is smooth at every embedded size.
    ss = 4
    big = Image.new("RGBA", (size * ss, size * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    accent = _hex_to_rgb(VIOLET_PRIMARY) + (255,)
    # A small inset keeps the circle off the very edge without reading as
    # padding.
    inset = max(0, round(size * ss * 0.06))
    d.ellipse((inset, inset, size * ss - 1 - inset, size * ss - 1 - inset), fill=accent)
    return big.resize((size, size), Image.LANCZOS)


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
