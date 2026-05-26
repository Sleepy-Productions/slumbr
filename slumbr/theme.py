"""Slumbr brand palette.

Single source of truth for colors. UI code should reference these names
instead of hard-coding hex strings, so a later accent-color picker can
mutate one place.

Palette is the Infinity Board violet (Sleepy Productions house style) —
see project memory `brand-palette` for provenance.
"""

from __future__ import annotations

# STARK BLACK & WHITE — yin-yang. No violet/purple, no blue tint, no muddy
# mid-grays in the chrome: pure-black surfaces, pure-white foreground, with a
# single neutral gray reserved ONLY for secondary text hierarchy. The names
# below are kept (legacy "VIOLET_*") but now hold a white→gray scale, so every
# reference across the app turns monochrome without touching call sites.
VIOLET_TINT = "#F2F2F2"
VIOLET_PALE = "#D4D4D4"
VIOLET_LIGHT = "#E6E6E6"
VIOLET_PRIMARY = "#FFFFFF"  # the "accent" — pure white (RECORDING dot, brand, active)
VIOLET_PRIMARY_HOVER = "#DBDBDB"  # filled-button hover
VIOLET_DEEP = "#9A9A9A"  # secondary state / pressed / TRANSCRIBING — neutral gray

# Dark surfaces — PURE BLACK, neutral. Cards sit a hair above black and are
# defined by a clearly-visible neutral border (white-line-on-black feel).
BG_DARK = "#000000"  # window backgrounds (pure black)
BG_PANEL = "#050505"  # elevated panels (popup pill)
BG_PANEL_HI = "#0E0E0E"  # cards / button rest
BORDER = "#3A3A3A"  # 1px dividers — neutral, clearly visible on black
TEXT_PRIMARY = "#FFFFFF"  # pure white body
TEXT_SECONDARY = "#9A9A9A"  # neutral gray — secondary text hierarchy only

# State semantics — referenced by tray and popup (monochrome)
COLOR_IDLE = "#6A6A6A"  # dim neutral gray (idle tray dot)
COLOR_RECORDING = VIOLET_PRIMARY     # white
COLOR_TRANSCRIBING = VIOLET_DEEP     # gray
COLOR_PASTING = VIOLET_DEEP          # gray
COLOR_SENT = "#5FB87A"  # green — brief "✓ Sent" flash (functional success signal)
COLOR_ERROR = "#E0685F"  # red — brief "✗ Failed" flash (functional error signal)


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


def text_on(bg_hex: str) -> str:
    """Readable foreground for text/icons placed ON a solid fill of ``bg_hex``.
    Returns near-black for a light fill, else the normal light body color — so
    a light/white accent yields dark text instead of invisible white-on-white
    (e.g. a white primary button keeps black text)."""
    try:
        r, g, b = _hex_to_rgb(bg_hex)
    except (ValueError, IndexError):
        return TEXT_PRIMARY
    # Perceptual (sRGB) luminance, 0..1.
    lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
    return "#0A0A0B" if lum > 0.6 else TEXT_PRIMARY


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
