"""Visual keyboard widget for binding the dictation hotkey.

Renders a full ANSI layout. Click any non-disabled key and we emit a
`key_chosen(int)` signal carrying the VK code. The currently-bound key
is highlighted in violet; disabled keys (modifiers, Backspace, Enter,
etc.) are muted and inert because they'd be terrible choices for a
single-key tap-to-toggle.

This widget is presentation-only — it knows nothing about the hotkey
hook. The hub binds it to `SlumbrConfig.hotkey_vk` and the Hotkey class.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..input.keymap import (
    DISABLED_VKS,
    LAYOUT_MAIN,
    vk_label,
)
from ..theme import (
    BG_PANEL,
    BG_PANEL_HI,
    BORDER,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    VIOLET_DEEP,
    VIOLET_PRIMARY,
)

# Base unit (one "1U" key in px). Tuned for a 900-ish-wide layout that
# breathes. Adjust this single number to scale the whole keyboard.
_U = 52
_GAP = 6


# Visual weights — how many units wide each variable-width key is.
# Keys not listed default to 1.0 U.
_WEIGHT: dict[str, float] = {
    "Backspace": 2.0,
    "Tab": 1.5,
    "\\": 1.5,
    "Caps Lock": 1.85,
    "Enter": 2.25,
    "Shift": 2.4,
    "Ctrl": 1.4,
    "Win": 1.3,
    "Alt": 1.4,
    "Menu": 1.3,
    "Space": 6.5,
}


def _key_width(label: str) -> int:
    return int(_U * _WEIGHT.get(label, 1.0))


class _KeyButton(QPushButton):
    """Single key. Uses a `data-state` dynamic property to drive styling."""

    def __init__(self, vk: int, label: str, sub: str = "") -> None:
        super().__init__()
        self._vk = vk
        self._label = label
        self._sub = sub
        self.setText(self._render_text())
        self.setFixedSize(_key_width(label), _U)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self._set_state("idle")
        f = QFont()
        f.setPointSize(10)
        self.setFont(f)
        if vk in DISABLED_VKS:
            self.setEnabled(False)
            self.setCursor(Qt.ForbiddenCursor)

    def _render_text(self) -> str:
        if self._sub:
            return f"{self._label}\n{self._sub}"
        return self._label

    def vk(self) -> int:
        return self._vk

    def _set_state(self, state: str) -> None:
        # `state` ∈ {"idle", "selected"}. QSS reads this via [data-state="..."].
        self.setProperty("data-state", state)
        self.style().unpolish(self)
        self.style().polish(self)

    def mark_selected(self, selected: bool) -> None:
        self._set_state("selected" if selected else "idle")


_QSS = f"""
QFrame#keyboard-frame {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 14px;
    padding: 18px;
}}
QPushButton {{
    background-color: {BG_PANEL_HI};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 0;
}}
QPushButton:hover {{
    border: 1px solid {VIOLET_PRIMARY};
}}
QPushButton:focus {{
    border: 1px solid {VIOLET_PRIMARY};
    outline: none;
}}
QPushButton:pressed {{
    background-color: {VIOLET_DEEP};
    border: 1px solid {VIOLET_DEEP};
}}
QPushButton:disabled {{
    color: {TEXT_SECONDARY};
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
}}
QPushButton[data-state="selected"] {{
    background-color: {VIOLET_PRIMARY};
    border: 1px solid {VIOLET_PRIMARY};
    color: {TEXT_PRIMARY};
    font-weight: 700;
}}
QPushButton[data-state="selected"]:hover {{
    background-color: {VIOLET_DEEP};
    border: 1px solid {VIOLET_DEEP};
}}
"""


class KeyboardPicker(QWidget):
    """Click a key to bind it. Emits the chosen VK code."""

    key_chosen = Signal(int)

    def __init__(self, current_vk: int) -> None:
        super().__init__()
        self._buttons: list[_KeyButton] = []
        self._current_vk = current_vk

        frame = QFrame()
        frame.setObjectName("keyboard-frame")
        frame.setStyleSheet(_QSS)

        rows_layout = QVBoxLayout(frame)
        rows_layout.setSpacing(_GAP)
        rows_layout.setContentsMargins(18, 18, 18, 18)

        for row in LAYOUT_MAIN:
            row_layout = QHBoxLayout()
            row_layout.setSpacing(_GAP)
            row_layout.setContentsMargins(0, 0, 0, 0)
            # Some rows (the Shift row) need to be flush-left + flush-right
            # rather than spread; the bottom row centers Space.
            for vk, label, sub in row:
                btn = _KeyButton(vk, label, sub)
                if btn.isEnabled():
                    btn.clicked.connect(lambda _checked=False, b=btn: self._on_key_clicked(b))
                self._buttons.append(btn)
                row_layout.addWidget(btn)
            row_layout.addStretch(1)
            rows_layout.addLayout(row_layout)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)

        # Current binding caption (lives below the keyboard so users see
        # what's bound without having to scan the highlight).
        self._caption = QLabel()
        cf = QFont()
        cf.setPointSize(10)
        self._caption.setFont(cf)
        self._caption.setStyleSheet(f"color: {TEXT_SECONDARY}; padding-top: 8px;")
        outer.addWidget(self._caption)

        self._refresh_selection()

    def _on_key_clicked(self, btn: _KeyButton) -> None:
        if btn.vk() == self._current_vk:
            return  # no-op rebind
        self._current_vk = btn.vk()
        self._refresh_selection()
        self.key_chosen.emit(btn.vk())

    def _refresh_selection(self) -> None:
        # `Shift` and `Ctrl` etc. appear twice (left + right) and share a
        # VK in our keymap — both should highlight when "selected", though
        # they're disabled anyway.
        for btn in self._buttons:
            btn.mark_selected(btn.vk() == self._current_vk)
        self._caption.setText(f"Currently bound: {vk_label(self._current_vk)}")

    def set_current_vk(self, vk: int) -> None:
        """External setter — keeps the keyboard in sync if config changes elsewhere."""
        self._current_vk = vk
        self._refresh_selection()
