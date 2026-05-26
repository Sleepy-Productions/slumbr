"""Shortcuts tab — visual keyboard picker for the dictation hotkey combo.

Builds a 1–4 key combo (single key like Caps Lock, or a chord like
Ctrl+Shift+J). The picker emits the full VK list; we surface the current
binding as a pill and forward changes to the app.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ...config import SlumbrConfig
from ...input.keymap import (
    combo_disabled_key_labels,
    combo_label,
    reserved_combo_names,
)
from ...theme import BG_PANEL, BORDER, TEXT_PRIMARY, TEXT_SECONDARY
from ..keyboard_picker import KeyboardPicker
from ._widgets import field_hint, heading, scrollable, subheading


class ShortcutsTab(QWidget):
    hotkey_changed = Signal(list)  # list[int] of VKs

    def __init__(self, config: SlumbrConfig) -> None:
        super().__init__()
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(56, 48, 56, 48)
        layout.setSpacing(20)

        layout.addWidget(heading("Shortcuts", size=28))
        layout.addWidget(
            subheading(
                "Click up to 4 keys to build your dictation hotkey — a single key "
                "(like Caps Lock) or a combo (like Ctrl + Shift + J). Click a "
                "selected key again to remove it. A combo frees up single keys for "
                "other apps and avoids conflicts. Modifiers in a combo keep working "
                "normally elsewhere — only the trigger key is consumed."
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
        self._bound_pill = QLabel(combo_label(config.hotkey_vks))
        self._bound_pill.setObjectName("hotkey-pill")
        binding_row.addWidget(binding_lead)
        binding_row.addWidget(self._bound_pill)
        binding_row.addStretch(1)
        layout.addLayout(binding_row)

        self._picker = KeyboardPicker(current_vks=config.hotkey_vks)
        self._picker.combo_changed.connect(self._on_picker_change)
        layout.addWidget(self._picker)

        layout.addWidget(self._build_blocked_panel())

        layout.addWidget(
            field_hint(
                "Changes take effect immediately — no restart. Whatever you bind is "
                "consumed by Slumbr while running; combo modifiers (Ctrl/Shift/Alt) "
                "still work normally everywhere else."
            )
        )
        layout.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scrollable(body))

    def _build_blocked_panel(self) -> QFrame:
        """A flat, always-visible disclaimer of what the picker refuses to
        bind — so a reserved key/combo silently doing nothing is never a
        mystery. Both lists are derived from the keymap, so this panel can't
        drift from what's actually enforced.
        """
        panel = QFrame()
        panel.setObjectName("blocked-panel")
        panel.setStyleSheet(
            f"""
            QFrame#blocked-panel {{
                background-color: {BG_PANEL};
                border: 1px solid {BORDER};
                border-radius: 12px;
            }}
            """
        )
        v = QVBoxLayout(panel)
        v.setContentsMargins(18, 14, 18, 14)
        v.setSpacing(6)

        title = QLabel("Auto-disabled — these never bind")
        tf = QFont()
        tf.setPointSize(10)
        tf.setBold(True)
        title.setFont(tf)
        title.setStyleSheet(f"color: {TEXT_PRIMARY};")
        v.addWidget(title)

        single = " · ".join(combo_disabled_key_labels())
        v.addWidget(
            field_hint(
                f"Single keys: {single} — they belong to every dialog and text box."
            )
        )

        combos = "  ·  ".join(reserved_combo_names())
        v.addWidget(
            field_hint(
                f"Reserved Windows shortcuts: {combos} — Slumbr would either break "
                "them or can't intercept them at all, so it won't take them."
            )
        )
        return panel

    def _on_picker_change(self, vks: list[int]) -> None:
        self._bound_pill.setText(combo_label(vks))
        self.hotkey_changed.emit(vks)

    def set_hotkey(self, vks: list[int]) -> None:
        self._picker.set_current_combo(vks)
        self._bound_pill.setText(combo_label(vks))
