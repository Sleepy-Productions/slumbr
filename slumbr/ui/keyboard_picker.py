"""Visual keyboard widget for binding the dictation hotkey combo.

Renders a full ANSI layout. Click keys to build a combo of 1–4 keys
(e.g. Ctrl + Shift + J); click a selected key again to remove it. Emits
``combo_changed(list[int])`` with the normalized VK codes. Selected keys
highlight in violet; a small set of keys (Esc / Enter / Tab / Backspace)
stay inert because binding them as a trigger is a footgun.

Modifiers (Ctrl / Shift / Alt / Win) ARE selectable now — that's the
point of combos. The hook passes them through normally and only consumes
the trigger key when the combo completes (see ``input/hotkey.py``).

Presentation-only — it knows nothing about the hook. The Shortcuts tab
wires it to ``SlumbrConfig.hotkey_vks`` and the ``Hotkey`` class.
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
    COMBO_DISABLED_VKS,
    LAYOUT_MAIN,
    MAX_COMBO_KEYS,
    combo_label,
    normalize_modifier,
    reserved_combo_name,
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

# Base unit (one "1U" key in px). Adjust this single number to scale the
# whole keyboard. Trimmed from 52 → 40 so the picker isn't bulky.
_U = 40
_GAP = 5


# Visual weights — how many units wide each variable-width key is.
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
        f.setPointSize(9)
        self.setFont(f)
        if normalize_modifier(vk) in COMBO_DISABLED_VKS or vk in COMBO_DISABLED_VKS:
            self.setEnabled(False)
            self.setCursor(Qt.ForbiddenCursor)

    def _render_text(self) -> str:
        if self._sub:
            return f"{self._label}\n{self._sub}"
        return self._label

    def vk(self) -> int:
        return self._vk

    def _set_state(self, state: str) -> None:
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
    """Click keys to build a 1–4 key combo. Emits the chosen VK list."""

    combo_changed = Signal(list)  # list[int] of normalized VKs

    def __init__(self, current_vks: list[int]) -> None:
        super().__init__()
        self._buttons: list[_KeyButton] = []
        self._combo: list[int] = [normalize_modifier(v) for v in current_vks if v]

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

        self._caption = QLabel()
        cf = QFont()
        cf.setPointSize(10)
        self._caption.setFont(cf)
        self._caption.setStyleSheet(f"color: {TEXT_SECONDARY}; padding-top: 8px;")
        outer.addWidget(self._caption)

        self._refresh_selection()

    def _on_key_clicked(self, btn: _KeyButton) -> None:
        vk = normalize_modifier(btn.vk())
        if vk in self._combo:
            # Toggle off — but never leave the combo empty.
            if len(self._combo) > 1:
                self._combo.remove(vk)
        elif len(self._combo) < MAX_COMBO_KEYS:
            # Refuse combos Windows owns (Win+L, Alt+F4, Ctrl+Alt+Del, …) —
            # we'd half-break them or couldn't hook them at all.
            reserved = reserved_combo_name(self._combo + [vk])
            if reserved is not None:
                self._caption.setText(
                    f"⚠  {reserved} is reserved by Windows — pick a different combo."
                )
                return
            self._combo.append(vk)
        else:
            # At the 4-key cap — ignore further additions.
            return
        self._refresh_selection()
        self.combo_changed.emit(list(self._combo))

    def _refresh_selection(self) -> None:
        for btn in self._buttons:
            btn.mark_selected(normalize_modifier(btn.vk()) in self._combo)
        full = " — combo full" if len(self._combo) >= MAX_COMBO_KEYS else ""
        self._caption.setText(f"Currently bound: {combo_label(self._combo)}{full}")

    def set_current_combo(self, vks: list[int]) -> None:
        """External setter — keep the keyboard in sync if config changes elsewhere."""
        self._combo = [normalize_modifier(v) for v in vks if v]
        self._refresh_selection()
