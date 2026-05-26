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

# Dark surfaces
BG_DARK = "#0D0D12"  # window backgrounds
BG_PANEL = "#16161C"  # elevated panels (popup)
BG_PANEL_HI = "#1E1E26"  # button rest
BORDER = "#3A3A44"  # 1px dividers
TEXT_PRIMARY = "#F3EDFF"  # white-ish for body
TEXT_SECONDARY = "#A09EB0"  # muted

# State semantics — referenced by tray and popup
COLOR_IDLE = "#9090A0"  # neutral gray; bumped from #7A7A82 for taskbar contrast
COLOR_RECORDING = VIOLET_PRIMARY
COLOR_TRANSCRIBING = VIOLET_DEEP
COLOR_PASTING = VIOLET_DEEP
COLOR_SENT = "#5FB87A"  # confident green — brief "✓ Sent" confirmation flash
