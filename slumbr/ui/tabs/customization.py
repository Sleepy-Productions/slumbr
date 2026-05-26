"""Customization tab — look & feel.

Split out of Behavior so that tab can stay focused on *how* Slumbr acts
(pasting, mic routing, reverse-PTT), while everything about how it *looks
and feels* lives here: the accent color and the recording-popup style.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...config import SlumbrConfig
from ...theme import BORDER
from ._widgets import field_hint, heading, scrollable, section_card, subheading


class CustomizationTab(QWidget):
    config_changed = Signal()

    def __init__(self, config: SlumbrConfig) -> None:
        super().__init__()
        self._config = config

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(40, 28, 40, 28)
        layout.setSpacing(16)

        layout.addWidget(heading("Customization", size=28))
        layout.addWidget(
            subheading("Make Slumbr yours — accent color and recording-popup style.")
        )

        # ===== Section: Appearance =====
        _card, sl = section_card("Appearance")
        sl.addWidget(
            field_hint(
                "Accent color — drives the whole app: the audio visualizer, the "
                "“✓ Sent” flash, the settings UI, the tray icon, and the engine "
                "chips. Changes apply live and persist across restarts."
            )
        )
        arow = QHBoxLayout()
        arow.setSpacing(10)
        arow.setContentsMargins(0, 4, 0, 0)
        self._color_swatch = QLabel()
        self._color_swatch.setFixedSize(30, 30)
        self._refresh_swatch()
        pick = QPushButton("Pick color…")
        pick.clicked.connect(self._on_pick_color)
        arow.addWidget(self._color_swatch)
        arow.addWidget(pick)
        arow.addStretch(1)
        sl.addLayout(arow)
        layout.addWidget(_card)

        # ===== Section: Recording popup =====
        _card, sl = section_card("Recording popup")
        self._compact_popup_cb = QCheckBox(
            "Compact recording popup (audio bars only — no word preview)"
        )
        self._compact_popup_cb.setChecked(config.compact_popup)
        self._compact_popup_cb.toggled.connect(self._on_compact_popup_toggle)
        sl.addWidget(self._compact_popup_cb)

        self._follow_cursor_cb = QCheckBox(
            "Popup follows the mouse cursor while recording"
        )
        self._follow_cursor_cb.setChecked(config.popup_follow_cursor)
        self._follow_cursor_cb.toggled.connect(self._on_follow_cursor_toggle)
        sl.addWidget(self._follow_cursor_cb)
        sl.addWidget(
            field_hint(
                "Off by default — if you dictate into a terminal, mouse motion "
                "events can leak as garbage text into your transcript."
            )
        )
        layout.addWidget(_card)

        layout.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scrollable(body))

    # ----------------------------------------------------------- handlers
    def _refresh_swatch(self) -> None:
        self._color_swatch.setStyleSheet(
            f"background: {self._config.accent_color}; border: 1px solid {BORDER}; "
            "border-radius: 6px;"
        )

    def _on_pick_color(self) -> None:
        chosen = QColorDialog.getColor(
            QColor(self._config.accent_color), self, "Pick accent color"
        )
        if chosen.isValid():
            self._config.accent_color = chosen.name()
            self._refresh_swatch()
            self.config_changed.emit()

    def _on_compact_popup_toggle(self, checked: bool) -> None:
        self._config.compact_popup = checked
        self.config_changed.emit()

    def _on_follow_cursor_toggle(self, checked: bool) -> None:
        self._config.popup_follow_cursor = checked
        self.config_changed.emit()

    def reflect_accent(self, primary: str) -> None:
        """Re-sync the swatch if the accent changed (kept for parity with the
        other tabs' live-recolor fan-out; the swatch reads from config)."""
        self._refresh_swatch()
