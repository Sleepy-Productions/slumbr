"""Shortcuts tab — visual keyboard picker for the dictation hotkey.

Refactor of the old ``ShortcutsPanel`` — visual style and KeyboardPicker
reuse unchanged.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ...config import SlumbrConfig
from ...input.keymap import vk_label
from ...theme import TEXT_SECONDARY
from ..keyboard_picker import KeyboardPicker
from ._widgets import field_hint, heading, scrollable, subheading


class ShortcutsTab(QWidget):
    hotkey_changed = Signal(int)

    def __init__(self, config: SlumbrConfig) -> None:
        super().__init__()
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(56, 48, 56, 48)
        layout.setSpacing(22)

        layout.addWidget(heading("Shortcuts", size=28))
        layout.addWidget(
            subheading(
                "Click any key on the keyboard below to bind your dictation hotkey. "
                "Modifiers (Shift, Ctrl, Alt, Win), Space, Enter, Tab, and Esc are "
                "disabled because they're load-bearing for normal typing."
            )
        )

        # Current binding pill
        binding_row = QHBoxLayout()
        binding_row.setSpacing(10)
        binding_row.setContentsMargins(0, 8, 0, 0)
        binding_lead = QLabel("Bound to")
        bl = QFont()
        bl.setPointSize(10)
        binding_lead.setFont(bl)
        binding_lead.setStyleSheet(f"color: {TEXT_SECONDARY};")
        self._bound_pill = QLabel(vk_label(config.hotkey_vk))
        self._bound_pill.setObjectName("hotkey-pill")
        binding_row.addWidget(binding_lead)
        binding_row.addWidget(self._bound_pill)
        binding_row.addStretch(1)
        layout.addLayout(binding_row)

        self._picker = KeyboardPicker(current_vk=config.hotkey_vk)
        self._picker.key_chosen.connect(self._on_picker_change)
        layout.addWidget(self._picker)

        layout.addWidget(
            field_hint(
                "The change takes effect immediately — no restart. Whatever you "
                "bind here is fully consumed by Slumbr while running (its normal "
                "OS behavior is suppressed)."
            )
        )
        layout.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scrollable(body))

    def _on_picker_change(self, vk: int) -> None:
        self._bound_pill.setText(vk_label(vk))
        self.hotkey_changed.emit(vk)

    def set_hotkey(self, vk: int) -> None:
        self._picker.set_current_vk(vk)
        self._bound_pill.setText(vk_label(vk))
