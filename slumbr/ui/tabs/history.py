"""History tab — last 50 transcripts with timestamps.

Replaces the "Last transcript" surface the old HomePanel owned. Entries
live at ``%APPDATA%\\Slumbr\\history.jsonl`` (see ``slumbr/history.py``).
"""

from __future__ import annotations

import time
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ... import history
from ...theme import BG_PANEL, BG_PANEL_HI, BORDER, TEXT_PRIMARY, TEXT_SECONDARY
from ._widgets import heading, scrollable, subheading


class HistoryTab(QWidget):
    history_cleared = Signal()

    def __init__(self) -> None:
        super().__init__()
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(56, 48, 56, 48)
        layout.setSpacing(22)

        # Header row with title + clear button
        header_row = QHBoxLayout()
        header_row.setSpacing(12)
        title = heading("History", size=28)
        header_row.addWidget(title)
        header_row.addStretch(1)
        self._clear_btn = QPushButton("Clear history")
        self._clear_btn.setObjectName("destructive")
        self._clear_btn.clicked.connect(self._on_clear_clicked)
        header_row.addWidget(self._clear_btn)
        layout.addLayout(header_row)

        layout.addWidget(
            subheading(
                "The last 50 transcripts Slumbr has produced. Local only — never "
                "sent anywhere."
            )
        )

        self._list = QListWidget()
        self._list.setStyleSheet(
            f"""
            QListWidget {{
                background: {BG_PANEL};
                border: 1px solid {BORDER};
                border-radius: 12px;
                padding: 8px;
                outline: 0;
            }}
            QListWidget::item {{
                color: {TEXT_PRIMARY};
                padding: 12px 14px;
                border-radius: 8px;
                margin: 2px 2px;
            }}
            QListWidget::item:selected {{
                background: {BG_PANEL_HI};
                color: {TEXT_PRIMARY};
            }}
            """
        )
        layout.addWidget(self._list, stretch=1)

        self._empty_label = QLabel(
            "No dictations yet. Tap your hotkey to start, then come back here."
        )
        self._empty_label.setStyleSheet(f"color: {TEXT_SECONDARY}; padding: 12px;")
        self._empty_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._empty_label)

        self.refresh()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scrollable(body))

    def refresh(self) -> None:
        """Re-read from disk. Called when the dialog opens + after clear."""
        entries = history.load_all()
        self._list.clear()
        for entry in reversed(entries):  # newest first
            stamp = _format_ts(entry.ts)
            text = entry.text if len(entry.text) <= 240 else entry.text[:240] + "…"
            item = QListWidgetItem(f"{stamp}   {text}")
            item.setToolTip(entry.text)
            self._list.addItem(item)
        self._empty_label.setVisible(not entries)
        self._list.setVisible(bool(entries))

    def _on_clear_clicked(self) -> None:
        history.clear()
        self.refresh()
        self.history_cleared.emit()


def _format_ts(ts: float) -> str:
    """Compact relative timestamp for the list. Today → 'HH:MM', earlier → date."""
    now = time.time()
    delta = now - ts
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta / 60)} min ago"
    today = datetime.fromtimestamp(now).date()
    when = datetime.fromtimestamp(ts)
    if when.date() == today:
        return when.strftime("%H:%M")
    return when.strftime("%b %d %H:%M")
