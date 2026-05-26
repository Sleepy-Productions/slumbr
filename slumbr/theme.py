"""Slumbr brand palette.

Single source of truth for colors. UI code should reference these names
instead of hard-coding hex strings, so a later accent-color picker can
mutate one place.

Palette is the Infinity Board violet (Sleepy Productions house style) —
see project memory `brand-palette` for provenance.
"""

from __future__ import annotations

# Violet scale, light → deep
VIOLET_TINT = "#F3EDFF"  # for text overlays
VIOLET_PALE = "#DCC8FF"  # for subtle highlights
VIOLET_LIGHT = "#CAA3FF"  # for hovers
VIOLET_PRIMARY = "#9B6FE0"  # accent — RECORDING dot, tray-active
VIOLET_PRIMARY_HOVER = "#AD84E8"  # filled-button hover (between primary and light)
VIOLET_DEEP = "#7B4FC0"  # TRANSCRIBING dot, borders

# Dark surfaces — near-black, neutral (no blue tint), matching the SleepyDev
# black+white house brand. Small steps between layers keep cards/borders
# legible without the surfaces reading as "gray".
BG_DARK = "#000000"  # window backgrounds (pure black)
BG_PANEL = "#08080A"  # elevated panels (popup pill)
BG_PANEL_HI = "#0D0D11"  # cards / button rest (outline-on-black look)
BORDER = "#2A2A30"  # 1px dividers — define cards on the black surface
TEXT_PRIMARY = "#F3EDFF"  # white-ish for body
TEXT_SECONDARY = "#A09EB0"  # muted

# State semantics — referenced by tray and popup
COLOR_IDLE = "#9090A0"  # neutral gray; bumped from #7A7A82 for taskbar contrast
COLOR_RECORDING = VIOLET_PRIMARY
COLOR_TRANSCRIBING = VIOLET_DEEP
COLOR_PASTING = VIOLET_DEEP
COLOR_SENT = "#5FB87A"  # confident green — brief "✓ Sent" confirmation flash
COLOR_ERROR = "#E0685F"  # red — brief "✗ Failed" flash when something goes wrong


# ---- accent derivation ----------------------------------------------------
# The user-chosen accent (config.accent_color, default = VIOLET_PRIMARY)
# drives the whole UI. From the single picked color we derive the lighter
# "hover" and darker "deep" shades the stylesheets need, so one pick recolors
# everything coherently.

def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _mix(rgb: tuple[float, float, float], target: tuple[int, int, int], t: float) -> str:
    r, g, b = (c + (tc - c) * t for c, tc in zip(rgb, target, strict=False))
    return f"#{int(round(r)):02X}{int(round(g)):02X}{int(round(b)):02X}"


def derive_accent(accent_hex: str) -> tuple[str, str, str, str]:
    """From one accent hex return ``(primary, hover, deep, pill_bg_rgba)``:
    the color itself, a lighter hover shade, a darker pressed/deep shade, and
    a translucent fill for the hotkey pill. Falls back to the house violet on
    a malformed value."""
    try:
        rgb = _hex_to_rgb(accent_hex)
    except (ValueError, IndexError):
        rgb = _hex_to_rgb(VIOLET_PRIMARY)
    primary = f"#{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"
    hover = _mix(rgb, (255, 255, 255), 0.16)  # 16% toward white
    deep = _mix(rgb, (0, 0, 0), 0.22)         # 22% toward black
    pill_bg = f"rgba({rgb[0]}, {rgb[1]}, {rgb[2]}, 40)"
    return primary, hover, deep, pill_bg
