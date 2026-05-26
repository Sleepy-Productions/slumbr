"""Brand-mark (moon-v2 ring) recoloring.

The vendored master art (assets/icon-master.png) is a glowing ring on an
opaque near-black square. ``colorized_glyph`` turns it into a ring of a chosen
color floating on transparency, cropped to fill. Every caller (the build-time
icon bake in scripts/build_icon.py, the runtime window icon, the About logo)
passes the FIXED brand color ``LOGO_COLOR`` (white) — the brand mark is
monochrome and does NOT follow the user's accent. (The function still takes a
color arg so the bake can be re-themed in one place if the brand ever changes.)

Pure PIL + numpy (no Qt) so the build script can import it without pulling in
PySide6; the Qt wrappers live in ui/_widgets.py.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

_MASTER = Path(__file__).resolve().parent / "assets" / "icon-master.png"

# The brand symbol is intentionally monochrome (clean black-and-white aesthetic)
# and FIXED — it does NOT follow the accent. The accent stays "your color" for
# the UI (tray dot, visualizer, chrome); the logo is the brand mark.
LOGO_COLOR = "#FFFFFF"


def _hex_rgb(s: str) -> tuple[int, int, int]:
    s = s.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


@lru_cache(maxsize=16)
def colorized_glyph(accent_hex: str, size: int = 256) -> Image.Image:
    """The moon-v2 ring recolored to ``accent_hex``, on transparency, square,
    cropped to fill. Cached per (color, size) — cheap to call repeatedly."""
    im = Image.open(_MASTER).convert("RGB")
    arr = np.asarray(im).astype(np.float32)
    # Brightness = max channel (captures the glow regardless of hue). Drives
    # both alpha (black backing -> transparent) and the tint intensity.
    t = arr.max(axis=2) / 255.0
    alpha = np.clip(t * 1.35, 0.0, 1.0)
    accent = np.array(_hex_rgb(accent_hex), dtype=np.float32)
    # The ring must read as the ACCENT color. The master ring is bright almost
    # everywhere, so any sizeable white lift washes the whole thing white — keep
    # it the accent and add only a faint sheen on the very brightest sliver for
    # a hint of glow.
    sheen = np.clip((t - 0.9) / 0.1, 0.0, 1.0) * 0.22
    base = accent[None, None, :] * np.clip(t * 1.3, 0.0, 1.0)[..., None]
    white = np.array([255.0, 255.0, 255.0])
    rgb = base * (1.0 - sheen[..., None]) + white * sheen[..., None]
    out = np.dstack([rgb, alpha * 255.0]).clip(0, 255).astype("uint8")
    img = Image.fromarray(out, "RGBA")
    # Crop to the ring (drop the transparent margin) so it fills the canvas.
    bbox = img.split()[3].point(lambda x: 255 if x > 28 else 0).getbbox()
    if bbox:
        img = img.crop(bbox)
    w, h = img.size
    side = max(w, h)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(img, ((side - w) // 2, (side - h) // 2))
    if side != size:
        square = square.resize((size, size), Image.LANCZOS)
    return square
