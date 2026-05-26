"""Bake slumbr/assets/icon.ico from icon-master.png.

Run once (or whenever the master art changes):
    .\\.venv\\Scripts\\python.exe scripts\\build_icon.py

The master is Slumbr's Sleepy Productions brand mark — "moon-v2", the glowing
violet circuit/glow ring (picked 2026-05-26). It's a rendered raster, so this
script bakes a multi-resolution .ico (16-256px) from it rather than drawing a
shape. Source of truth for the art: the brand-kit (sleepy-productions); the
master is vendored here as slumbr/assets/icon-master.png so the build is
self-contained.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "slumbr" / "assets"
MASTER = ASSETS / "icon-master.png"
OUT_PATH = ASSETS / "icon.ico"

SIZES = [16, 24, 32, 48, 64, 128, 256]


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    im = Image.open(MASTER).convert("RGBA")
    # Pillow's ICO encoder embeds every requested size, downscaled with LANCZOS.
    im.save(OUT_PATH, format="ICO", sizes=[(s, s) for s in SIZES])
    im.resize((256, 256), Image.LANCZOS).save(ASSETS / "icon.png")
    print(f"wrote {OUT_PATH} (and icon.png) from {MASTER.name} with sizes {SIZES}")


if __name__ == "__main__":
    main()
