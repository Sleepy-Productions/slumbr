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

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "slumbr" / "assets"
MASTER = ASSETS / "icon-master.png"
OUT_PATH = ASSETS / "icon.ico"

SIZES = [16, 24, 32, 48, 64, 128, 256]


def _to_glow_on_transparent(im: Image.Image) -> Image.Image:
    """The moon-v2 master is a glowing violet ring on an opaque near-black
    square — which reads as "a square inside a square" against the UI and
    doesn't fill the icon. Turn the black backing into transparency and crop
    to the ring so it fills the canvas:

      - Alpha = the brightest color channel (NOT luma — luma underweights
        violet/magenta and would make the ring faint). Black backing -> 0,
        the dark hole inside the ring -> ~0, the glowing ring -> opaque, and
        the glow falls off softly to transparent. Result: a ring floating on
        transparency that blends on any background.
      - Crop to the ring's bounding box + pad back to square so it fills
        the icon edge-to-edge instead of sitting small in a margin.
    """
    im = im.convert("RGBA")
    r, g, b, _ = im.split()
    maxc = ImageChops.lighter(ImageChops.lighter(r, g), b)
    # Lift the glow a touch so the ring stays solid while the falloff fades out.
    alpha = maxc.point(lambda x: min(255, int(x * 1.35)))
    im.putalpha(alpha)
    # Crop to meaningful content (ignore faint stray pixels under the threshold).
    mask = alpha.point(lambda x: 255 if x > 28 else 0)
    bbox = mask.getbbox()
    if bbox:
        im = im.crop(bbox)
    w, h = im.size
    side = max(w, h)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(im, ((side - w) // 2, (side - h) // 2))
    return square


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    im = _to_glow_on_transparent(Image.open(MASTER))
    # Pillow's ICO encoder embeds every requested size, downscaled with LANCZOS.
    im.save(OUT_PATH, format="ICO", sizes=[(s, s) for s in SIZES])
    im.resize((256, 256), Image.LANCZOS).save(ASSETS / "icon.png")
    print(f"wrote {OUT_PATH} (and icon.png) from {MASTER.name} with sizes {SIZES}")


if __name__ == "__main__":
    main()
