"""Bake slumbr/assets/icon.ico + icon.png from the moon-v2 master.

This is the STATIC shell icon — Explorer, the pinned taskbar entry, and the
frozen exe's embedded icon — tinted to the brand violet. At RUNTIME the app
re-tints the same master to the user's chosen accent for the window/taskbar
icon and the About logo (see slumbr/branding.py + ui/_widgets.glyph_icon), so
the live symbol always matches the accent; this baked file is just the
pre-launch fallback.

Run when the master art or brand color changes:
    .\\.venv\\Scripts\\python.exe scripts\\build_icon.py
"""

from __future__ import annotations

from pathlib import Path

from slumbr.branding import LOGO_COLOR, colorized_glyph

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "slumbr" / "assets"
OUT_PATH = ASSETS / "icon.ico"

SIZES = [16, 24, 32, 48, 64, 128, 256]


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    im = colorized_glyph(LOGO_COLOR, 256)
    # Pillow's ICO encoder embeds every requested size, downscaled with LANCZOS.
    im.save(OUT_PATH, format="ICO", sizes=[(s, s) for s in SIZES])
    im.save(ASSETS / "icon.png")
    print(f"wrote {OUT_PATH} (and icon.png) — monochrome {LOGO_COLOR}, sizes {SIZES}")


if __name__ == "__main__":
    main()
