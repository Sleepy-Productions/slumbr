"""Windows Virtual-Key codes and human-readable labels.

Used by the hotkey picker UI and the configurable hotkey hook. We list
only keys that make sense as a dictation toggle — modifiers, mouse
buttons, IME keys, and OEM-locale-specific keys are intentionally
excluded. If a user wants something exotic, they can edit the VK code
in `config.json` directly.

VK codes are stable Windows ABI; see
https://learn.microsoft.com/windows/win32/inputdev/virtual-key-codes
"""

from __future__ import annotations

# (vk, primary_label, optional_subtitle)
# Subtitle is shown below the label in the keyboard picker for keys
# whose VK name is non-obvious.
_Key = tuple[int, str, str]


def k(vk: int, label: str, sub: str = "") -> _Key:
    return (vk, label, sub)


# ---- function row ---------------------------------------------------------
ROW_FUNCTION: list[_Key] = [
    k(0x1B, "Esc"),
    k(0x70, "F1"),
    k(0x71, "F2"),
    k(0x72, "F3"),
    k(0x73, "F4"),
    k(0x74, "F5"),
    k(0x75, "F6"),
    k(0x76, "F7"),
    k(0x77, "F8"),
    k(0x78, "F9"),
    k(0x79, "F10"),
    k(0x7A, "F11"),
    k(0x7B, "F12"),
]

# ---- number row -----------------------------------------------------------
ROW_NUMBERS: list[_Key] = [
    k(0xC0, "`"),
    k(0x31, "1"),
    k(0x32, "2"),
    k(0x33, "3"),
    k(0x34, "4"),
    k(0x35, "5"),
    k(0x36, "6"),
    k(0x37, "7"),
    k(0x38, "8"),
    k(0x39, "9"),
    k(0x30, "0"),
    k(0xBD, "-"),
    k(0xBB, "="),
    k(0x08, "Backspace"),
]

# ---- top alpha row (Q-row) ------------------------------------------------
ROW_QWERTY: list[_Key] = [
    k(0x09, "Tab"),
    k(0x51, "Q"),
    k(0x57, "W"),
    k(0x45, "E"),
    k(0x52, "R"),
    k(0x54, "T"),
    k(0x59, "Y"),
    k(0x55, "U"),
    k(0x49, "I"),
    k(0x4F, "O"),
    k(0x50, "P"),
    k(0xDB, "["),
    k(0xDD, "]"),
    k(0xDC, "\\"),
]

# ---- home row -------------------------------------------------------------
ROW_HOME: list[_Key] = [
    k(0x14, "Caps Lock", "default"),
    k(0x41, "A"),
    k(0x53, "S"),
    k(0x44, "D"),
    k(0x46, "F"),
    k(0x47, "G"),
    k(0x48, "H"),
    k(0x4A, "J"),
    k(0x4B, "K"),
    k(0x4C, "L"),
    k(0xBA, ";"),
    k(0xDE, "'"),
    k(0x0D, "Enter"),
]

# ---- bottom alpha row -----------------------------------------------------
ROW_ZXCV: list[_Key] = [
    k(0x10, "Shift"),
    k(0x5A, "Z"),
    k(0x58, "X"),
    k(0x43, "C"),
    k(0x56, "V"),
    k(0x42, "B"),
    k(0x4E, "N"),
    k(0x4D, "M"),
    k(0xBC, ","),
    k(0xBE, "."),
    k(0xBF, "/"),
    k(0x10, "Shift"),
]

# ---- bottom-most row (modifiers + space) ----------------------------------
ROW_BOTTOM: list[_Key] = [
    k(0x11, "Ctrl"),
    k(0x5B, "Win"),
    k(0x12, "Alt"),
    k(0x20, "Space"),
    k(0x12, "Alt"),
    k(0x5D, "Menu"),
    k(0x11, "Ctrl"),
]

# ---- nav cluster (right of the main block) --------------------------------
ROW_NAV_TOP: list[_Key] = [
    k(0x2D, "Insert"),
    k(0x24, "Home"),
    k(0x21, "PgUp"),
]
ROW_NAV_MID: list[_Key] = [
    k(0x2E, "Delete"),
    k(0x23, "End"),
    k(0x22, "PgDn"),
]
ROW_ARROWS: list[list[_Key]] = [
    [k(0x26, "↑")],
    [k(0x25, "←"), k(0x28, "↓"), k(0x27, "→")],
]

# Full rendering order used by the picker
LAYOUT_MAIN: list[list[_Key]] = [
    ROW_FUNCTION,
    ROW_NUMBERS,
    ROW_QWERTY,
    ROW_HOME,
    ROW_ZXCV,
    ROW_BOTTOM,
]


_FALLBACK_VK_LABEL: dict[int, str] = {}
for row in LAYOUT_MAIN + [ROW_NAV_TOP, ROW_NAV_MID, *ROW_ARROWS]:
    for vk, label, _sub in row:
        _FALLBACK_VK_LABEL.setdefault(vk, label)


def vk_label(vk: int) -> str:
    """Friendly label for a VK code (for display in the hub status / About)."""
    return _FALLBACK_VK_LABEL.get(vk, f"VK {vk:#04x}")


# Modifier VKs that are bad choices for a *single-key* tap-to-toggle — we
# disable them in the picker so the user can't accidentally bind Shift /
# Ctrl / Alt / Win as their dictation key. They'd never be able to type
# again.
DISABLED_VKS: frozenset[int] = frozenset(
    {
        0x10,  # Shift (either)
        0xA0,  # Left Shift
        0xA1,  # Right Shift
        0x11,  # Ctrl (either)
        0xA2,  # Left Ctrl
        0xA3,  # Right Ctrl
        0x12,  # Alt (either)
        0xA4,  # Left Alt
        0xA5,  # Right Alt
        0x5B,  # Left Win
        0x5C,  # Right Win
        0x08,  # Backspace — too disruptive to suppress
        0x0D,  # Enter — same
        0x09,  # Tab — same
        0x1B,  # Esc — used by many modal dialogs
        0x20,  # Space — would break typing entirely
    }
)
