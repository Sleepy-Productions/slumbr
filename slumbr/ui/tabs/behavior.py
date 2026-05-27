"""Behavior tab — how the transcript gets inserted.

Just the paste method. The "mute me while dictating" virtual-cable routing moved
to the Voice tab (it's all mic-input config); paste extras live in Advanced.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ...config import SlumbrConfig
from ._widgets import (
    NoScrollComboBox,
    field_label,
    heading,
    scrollable,
    section_card,
    subheading,
)


class BehaviorTab(QWidget):
    config_changed = Signal()

    def __init__(self, config: SlumbrConfig) -> None:
        super().__init__()
        self._config = config

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(48, 40, 48, 40)
        layout.setSpacing(20)

        layout.addWidget(heading("Behavior", size=28))
        layout.addWidget(
            subheading(
                "How Slumbr inserts the transcript. Mic routing lives in Voice; "
                "fine-tuning lives in the Advanced tab."
            )
        )

        # ===== Pasting =====
        _card, sl = section_card("Pasting")
        sl.addWidget(field_label("Paste method"))
        self._paste_combo = NoScrollComboBox()
        self._paste_combo.addItem("Ctrl+V — default, works almost everywhere", userData="ctrl_v")
        self._paste_combo.addItem(
            "Ctrl+Shift+V — fallback if an app ignores Ctrl+V", userData="ctrl_shift_v"
        )
        self._paste_combo.addItem("Type each character — universal, slower", userData="type")
        i = self._paste_combo.findData(config.paste_method)
        if i >= 0:
            self._paste_combo.setCurrentIndex(i)
        self._paste_combo.currentIndexChanged.connect(self._on_paste_changed)
        sl.addWidget(self._paste_combo)
        layout.addWidget(_card)

        layout.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scrollable(body))

    # ----------------------------------------------------------- handlers
    def _on_paste_changed(self, *_args) -> None:
        method = self._paste_combo.currentData()
        if method:
            self._config.paste_method = method
        self.config_changed.emit()
