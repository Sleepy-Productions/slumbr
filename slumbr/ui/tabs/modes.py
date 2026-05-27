"""Modes tab — pick the active dictation persona + the Code-mode reference.

A *mode* is a full persona: its own STT language, vocabulary hint, text
formatter (prose vs code), and paste behavior. This tab is the home for:

  • choosing which mode is active (radio group; mirrors the tray submenu),
  • a quick read-out of what each mode does,
  • the Code-mode spoken-symbol cheat sheet, and
  • an optional global hotkey that cycles modes.

The per-mode *field editing* (language, vocabulary, paste method, auto-send)
lives on the Voice / Behavior / Advanced tabs, which follow the active mode —
so there's a single editor per field, no duplication. Switching the active
mode here tells the dialog to reload those tabs.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ...config import SlumbrConfig
from ...theme import BG_PANEL, BORDER, RADIUS_MD, TEXT_PRIMARY
from ..keyboard_picker import KeyboardPicker
from ._widgets import (
    field_hint,
    field_label,
    heading,
    scrollable,
    section_card,
    subheading,
)

_FORMATTER_LABEL = {"prose": "Prose (sentences)", "code": "Code grammar"}

_CODE_CHEATSHEET = (
    "Brackets   open / close  paren ( )   bracket [ ]   brace { }\n"
    "Punctuation   semicolon ;   colon :   comma ,   dot .   underscore _\n"
    "Operators   equals =   plus +   minus -   star *   slash /   percent %\n"
    "            arrow ->   fat arrow =>   double equals ==   not equals !=\n"
    "Casing   camel case  → fooBar      snake case  → foo_bar\n"
    "         pascal case → FooBar      constant case → FOO_BAR\n"
    "Structure   new line    tab\n"
    '\nExample:  "def foo open paren x close paren colon new line return x"\n'
    "      →   def foo(x):\n              return x"
)


class ModesTab(QWidget):
    config_changed = Signal()
    active_mode_changed = Signal()  # tells the dialog to reload the per-mode tabs

    def __init__(self, config: SlumbrConfig) -> None:
        super().__init__()
        self._config = config
        self._cycle_vks: list[int] = list(config.cycle_mode_vks)

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(48, 40, 48, 40)
        layout.setSpacing(20)

        layout.addWidget(heading("Modes", size=28))
        layout.addWidget(
            subheading(
                "A mode is a full dictation persona — its own language, vocabulary, "
                "text formatting, and paste behavior. Switch from the tray, a cycle "
                "hotkey, or here. Edit the active mode's language & vocabulary on the "
                "Voice and Advanced tabs, and its paste behavior on Behavior."
            )
        )

        # ===== Active mode =====
        _card, sl = section_card("Active mode")
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for m in config.modes:
            rb = QRadioButton(m.label)
            rb.setChecked(m.id == config.active_mode)
            rb.toggled.connect(lambda checked, mid=m.id: checked and self._on_mode_picked(mid))
            self._group.addButton(rb)
            sl.addWidget(rb)
            sl.addWidget(field_hint(self._describe(m)))
        layout.addWidget(_card)

        # ===== Code reference =====
        _card, sl = section_card("Code mode — spoken symbols")
        sl.addWidget(
            field_hint(
                "When Code mode is active, say the symbol or the casing and Slumbr "
                "lays it out as source instead of a sentence (no auto-capitalization "
                "or trailing period)."
            )
        )
        ref = QLabel(_CODE_CHEATSHEET)
        ref.setWordWrap(False)
        rf = QFont("Consolas")
        rf.setPointSize(9)
        ref.setFont(rf)
        ref.setStyleSheet(
            f"color: {TEXT_PRIMARY}; background: {BG_PANEL}; border: 1px solid {BORDER}; "
            f"border-radius: {RADIUS_MD}px; padding: 14px;"
        )
        sl.addWidget(ref)
        layout.addWidget(_card)

        # ===== Cycle hotkey (optional) =====
        _card, sl = section_card("Cycle hotkey (optional)")
        self._cycle_cb = QCheckBox("Enable a hotkey that jumps to the next mode")
        self._cycle_cb.setChecked(bool(config.cycle_mode_vks))
        self._cycle_cb.toggled.connect(self._on_cycle_toggle)
        sl.addWidget(self._cycle_cb)
        sl.addWidget(field_label("Cycle combo"))
        self._cycle_picker = KeyboardPicker(current_vks=config.cycle_mode_vks)
        self._cycle_picker.combo_changed.connect(self._on_cycle_combo)
        self._cycle_picker.setEnabled(bool(config.cycle_mode_vks))
        sl.addWidget(self._cycle_picker)
        sl.addWidget(
            field_hint(
                "Leave this off if you'd rather switch from the tray's Mode submenu. "
                "Pick a combo (e.g. Ctrl + Shift + M) so it doesn't clash with typing."
            )
        )
        layout.addWidget(_card)

        layout.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scrollable(body))

    def _describe(self, mode) -> str:
        fmt = _FORMATTER_LABEL.get(mode.formatter, mode.formatter)
        lang = mode.language or "auto-detect"
        send = "auto-sends" if mode.auto_send else "no auto-send"
        return f"{fmt} · language {lang} · {send}"

    # ----------------------------------------------------------- handlers
    def _on_mode_picked(self, mode_id: str) -> None:
        if mode_id == self._config.active_mode:
            return
        self._config.active_mode = mode_id
        # Reload the per-mode tabs FIRST so their widgets show the new mode,
        # then run the normal save + runtime-retune path.
        self.active_mode_changed.emit()
        self.config_changed.emit()

    def _on_cycle_toggle(self, checked: bool) -> None:
        self._cycle_picker.setEnabled(checked)
        self._config.cycle_mode_vks = list(self._cycle_vks) if checked else []
        self.config_changed.emit()

    def _on_cycle_combo(self, vks: list[int]) -> None:
        self._cycle_vks = list(vks)
        if self._cycle_cb.isChecked():
            self._config.cycle_mode_vks = list(vks)
            self.config_changed.emit()

    def reflect_accent(self, primary: str, deep: str) -> None:
        self._cycle_picker.reflect_accent(primary, deep)
