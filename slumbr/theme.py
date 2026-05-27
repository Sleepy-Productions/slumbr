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
COLOR_RECORDING = VIOLET_PRIMARY  # white
COLOR_TRANSCRIBING = VIOLET_DEEP  # gray
COLOR_PASTING = VIOLET_DEEP  # gray
COLOR_SENT = "#5FB87A"  # green — brief "✓ Sent" flash (functional success signal)
COLOR_ERROR = "#E0685F"  # red — brief "✗ Failed" flash (functional error signal)

TEXT_DISABLED = "#5A5A5A"  # greyed-out controls (distinct from secondary text)


# ---- typography + scale tokens --------------------------------------------
# House fonts (bundled in assets/fonts/, registered at startup by
# load_app_fonts). Sora = display/headings, Inter = body/UI; Consolas stays the
# mono face (timestamps, keycaps). QSS family strings list a system fallback so
# the UI still renders if registration ever fails.
FONT_DISPLAY = "Sora"
FONT_BODY = "Inter"
FONT_MONO = "Consolas"

# Spacing: 8pt grid (4pt sub-steps). Reference these instead of ad-hoc px.
SPACE_XS, SPACE_SM, SPACE_MD, SPACE_LG, SPACE_XL = 4, 8, 12, 16, 24

# Radius scale — one default, collapsing the old 5/6/10/11/12 sprawl.
RADIUS_XS = 6  # small chips, checkbox indicator
RADIUS_MD = 8  # default: buttons, combos
RADIUS_CARD = 12  # cards, text areas, lists
RADIUS_PILL = 999

_FONTS_LOADED = False


def load_app_fonts(app) -> None:
    """Register the bundled house fonts and make Inter the application default
    family (size unchanged). Call once, right after QApplication is created.
    Idempotent and defensive: on any failure the UI keeps the system font."""
    global _FONTS_LOADED
    if _FONTS_LOADED:
        return
    try:
        from pathlib import Path

        from PySide6.QtGui import QFontDatabase

        fonts_dir = Path(__file__).parent / "assets" / "fonts"
        for ttf in ("Inter-Regular.ttf", "Inter-SemiBold.ttf", "Sora.ttf"):
            QFontDatabase.addApplicationFont(str(fonts_dir / ttf))
        f = app.font()
        f.setFamily(FONT_BODY)  # family only — preserve Qt's default point size
        app.setFont(f)
        _FONTS_LOADED = True
    except Exception:
        # Never let a font hiccup block startup — Segoe UI remains the fallback.
        pass


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


def apply_dark_palette(app) -> None:
    """Set a black QPalette on the QApplication. Qt-Style-Sheets only color the
    widgets they explicitly target; everything else (scroll-area viewports,
    item-view gaps, tooltips, default widget backgrounds) falls back to the
    PALETTE, which is OS-default GREY on Windows. Without this, those surfaces
    render grey behind the black-styled cards. Call once, right after creating
    the QApplication (in the real app AND any screenshot harness, so captures
    match)."""
    from PySide6.QtGui import QColor, QPalette

    p = app.palette()
    p.setColor(QPalette.Window, QColor(BG_DARK))
    p.setColor(QPalette.Base, QColor(BG_DARK))
    p.setColor(QPalette.AlternateBase, QColor(BG_PANEL))
    p.setColor(QPalette.Button, QColor(BG_PANEL_HI))
    p.setColor(QPalette.WindowText, QColor(TEXT_PRIMARY))
    p.setColor(QPalette.Text, QColor(TEXT_PRIMARY))
    p.setColor(QPalette.ButtonText, QColor(TEXT_PRIMARY))
    p.setColor(QPalette.ToolTipBase, QColor(BG_PANEL))
    p.setColor(QPalette.ToolTipText, QColor(TEXT_PRIMARY))
    p.setColor(QPalette.PlaceholderText, QColor(TEXT_SECONDARY))
    app.setPalette(p)


def text_on(bg_hex: str) -> str:
    """Readable foreground for text/icons placed ON a solid fill of ``bg_hex``.
    Returns near-black for a light fill, else the normal light body color — so
    a light/white accent yields dark text instead of invisible white-on-white
    (e.g. a white primary button keeps black text)."""
    try:
        r, g, b = _hex_to_rgb(bg_hex)
    except (ValueError, IndexError, TypeError, AttributeError):
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
    except (ValueError, IndexError, TypeError, AttributeError):
        # Malformed OR wrong-typed (a corrupt config can hand us a number/None,
        # which would raise AttributeError on .lstrip()) — fall back to default.
        rgb = _hex_to_rgb(VIOLET_PRIMARY)
    primary = f"#{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"
    hover = _mix(rgb, (255, 255, 255), 0.16)  # 16% toward white
    deep = _mix(rgb, (0, 0, 0), 0.22)  # 22% toward black
    pill_bg = f"rgba({rgb[0]}, {rgb[1]}, {rgb[2]}, 40)"
    return primary, hover, deep, pill_bg
