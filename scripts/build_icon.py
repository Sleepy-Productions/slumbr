"""Bake slumbr/assets/icon.ico + icon.png from the moon-v2 master.

This is the STATIC shell icon — Explorer, the desktop shortcut, the pinned
taskbar entry. It is baked in the FIXED brand color (branding.LOGO_COLOR =
white), monochrome. At RUNTIME the app renders the same master in that SAME
fixed brand color for the window/taskbar icon and the About logo (see
slumbr/branding.py + ui/tabs/_widgets.glyph_icon) — the brand mark does NOT
follow the user's accent. The accent is "your color" for the chrome only
(tray dot, visualizer); a tinted/pink icon is a bug.

NOTE: overwriting icon.ico does not always evict Windows' shell icon cache —
install.ps1's Reset-IconCache handles that so a stale icon can't linger.

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
