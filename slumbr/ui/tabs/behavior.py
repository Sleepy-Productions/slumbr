"""Behavior tab — paste method, auto-send, clipboard preservation.

The old ``BehaviorPanel`` also owned the close-to-tray toggle; that
field is dead in the post-rearch UI (no hub window to close) but the
field remains on ``SlumbrConfig`` for backwards-compat. We don't
expose it here.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QVBoxLayout, QWidget

from ...config import SlumbrConfig
from ._widgets import field_label, heading, scrollable, subheading


class BehaviorTab(QWidget):
    config_changed = Signal()

    def __init__(self, config: SlumbrConfig) -> None:
        super().__init__()
        self._config = config

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(56, 48, 56, 48)
        layout.setSpacing(22)

        layout.addWidget(heading("Behavior", size=28))
        layout.addWidget(
            subheading(
                "How Slumbr inserts the transcript into the focused window."
            )
        )

        # Paste method
        layout.addWidget(field_label("Paste method"))
        self._paste_combo = QComboBox()
        self._paste_combo.addItem(
            "Ctrl+V — chats, browsers, editors (fastest)", userData="ctrl_v"
        )
        self._paste_combo.addItem(
            "Ctrl+Shift+V — terminals (VS Code, Windows Terminal)",
            userData="ctrl_shift_v",
        )
        self._paste_combo.addItem(
            "Type each character — universal, slower", userData="type"
        )
        i = self._paste_combo.findData(config.paste_method)
        if i >= 0:
            self._paste_combo.setCurrentIndex(i)
        self._paste_combo.currentIndexChanged.connect(self._on_changed)
        layout.addWidget(self._paste_combo)

        # Auto-send
        self._auto_send_cb = QCheckBox(
            "Press Enter after pasting (auto-send for chat apps)"
        )
        self._auto_send_cb.setChecked(config.auto_send)
        self._auto_send_cb.toggled.connect(self._on_changed)
        layout.addWidget(self._auto_send_cb)

        # Preserve clipboard
        self._preserve_cb = QCheckBox(
            "Restore previous clipboard contents after pasting"
        )
        self._preserve_cb.setChecked(config.preserve_clipboard)
        self._preserve_cb.toggled.connect(self._on_changed)
        layout.addWidget(self._preserve_cb)

        layout.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scrollable(body))

    def _on_changed(self, *_args) -> None:
        method = self._paste_combo.currentData()
        if method:
            self._config.paste_method = method
        self._config.auto_send = self._auto_send_cb.isChecked()
        self._config.preserve_clipboard = self._preserve_cb.isChecked()
        self.config_changed.emit()
