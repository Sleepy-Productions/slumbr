"""History tab — last 30 transcripts with timestamps.

Replaces the "Last transcript" surface the old HomePanel owned. Entries
live at ``%APPDATA%\\Slumbr\\history.jsonl`` (see ``slumbr/history.py``).
"""

from __future__ import annotations

import time
from datetime import datetime

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from ... import history
from ...theme import BG_PANEL, BG_PANEL_HI, BORDER, TEXT_PRIMARY, TEXT_SECONDARY
from ._widgets import heading, scrollable, subheading, tag

# Per-item data roles: the delegate paints the timestamp + transcript in two
# different styles, so we stash them separately instead of one display string.
_TS_ROLE = Qt.UserRole + 1
_TEXT_ROLE = Qt.UserRole + 2


class _HistoryDelegate(QStyledItemDelegate):
    """Paints each history row as a dim monospace timestamp + the transcript
    in primary text, with the transcript elided to the row width. Reads much
    cleaner than one flat ``"13:42   text"`` string and never wraps oddly."""

    PAD_X = 14
    TS_W = 80
    GAP = 16
    ROW_H = 42

    def paint(self, painter, option, index) -> None:  # noqa: N802
        painter.save()
        rect = option.rect
        if option.state & (QStyle.State_Selected | QStyle.State_MouseOver):
            painter.setRenderHint(painter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(BG_PANEL_HI))
            painter.drawRoundedRect(rect.adjusted(4, 2, -4, -2), 8, 8)

        stamp = index.data(_TS_ROLE) or ""
        text = index.data(_TEXT_ROLE) or ""

        ts_font = QFont("Consolas")
        ts_font.setPointSize(9)
        painter.setFont(ts_font)
        painter.setPen(QColor(TEXT_SECONDARY))
        ts_rect = QRect(rect.left() + self.PAD_X, rect.top(), self.TS_W, rect.height())
        painter.drawText(ts_rect, Qt.AlignRight | Qt.AlignVCenter, stamp)

        body_font = QFont()
        body_font.setPointSize(10)
        painter.setFont(body_font)
        painter.setPen(QColor(TEXT_PRIMARY))
        bx = rect.left() + self.PAD_X + self.TS_W + self.GAP
        body_rect = QRect(bx, rect.top(), rect.right() - bx - self.PAD_X, rect.height())
        elided = QFontMetrics(body_font).elidedText(
            text, Qt.ElideRight, body_rect.width()
        )
        painter.drawText(body_rect, Qt.AlignLeft | Qt.AlignVCenter, elided)
        painter.restore()

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        return QSize(0, self.ROW_H)


class HistoryTab(QWidget):
    history_cleared = Signal()

    def __init__(self) -> None:
        super().__init__()
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(48, 40, 48, 40)
        layout.setSpacing(20)

        # Header row with title + clear button
        header_row = QHBoxLayout()
        header_row.setSpacing(12)
        title = heading("History", size=28)
        header_row.addWidget(title)
        header_row.addStretch(1)
        self._count_tag = tag("0")
        header_row.addWidget(self._count_tag)
        self._copy_all_btn = QPushButton("Copy all")
        self._copy_all_btn.clicked.connect(self._on_copy_all)
        header_row.addWidget(self._copy_all_btn)
        self._clear_btn = QPushButton("Clear history")
        self._clear_btn.setObjectName("destructive")
        self._clear_btn.clicked.connect(self._on_clear_clicked)
        header_row.addWidget(self._clear_btn)
        layout.addLayout(header_row)

        layout.addWidget(
            subheading(
                "The last 30 transcripts Slumbr has produced — older ones clear "
                "automatically. Local only, never sent anywhere. "
                "Double-click, right-click, or select + Ctrl+C to copy a line."
            )
        )

        self._list = QListWidget()
        self._list.setItemDelegate(_HistoryDelegate())
        self._list.setMouseTracking(True)  # so rows highlight on hover
        # Copy a transcript out: double-click, right-click, or select + Ctrl+C.
        # (A dictation that got "sent" with no field focused still lands here,
        # so it needs to be recoverable without digging through the log file.)
        self._list.itemDoubleClicked.connect(self._copy_item)
        self._list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._show_context_menu)
        _copy_sc = QShortcut(QKeySequence.StandardKey.Copy, self._list)
        _copy_sc.activated.connect(self._copy_selected)
        self._list.setStyleSheet(
            f"""
            QListWidget {{
                background: {BG_PANEL};
                border: 1px solid {BORDER};
                border-radius: 12px;
                padding: 8px;
                outline: 0;
            }}
            """
        )
        layout.addWidget(self._list, stretch=1)

        self._empty_label = QLabel(
            "No dictations yet.\n\nTap your hotkey to start dictating — your "
            "transcripts will show up here."
        )
        ef = QFont()
        ef.setPointSize(11)
        self._empty_label.setFont(ef)
        self._empty_label.setStyleSheet(f"color: {TEXT_SECONDARY}; padding: 36px;")
        self._empty_label.setAlignment(Qt.AlignCenter)
        self._empty_label.setWordWrap(True)
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
            item = QListWidgetItem()
            item.setData(_TS_ROLE, _format_ts(entry.ts))
            item.setData(_TEXT_ROLE, entry.text)
            item.setToolTip(entry.text)
            self._list.addItem(item)
        cap = getattr(history, "MAX_ENTRIES", 30)
        self._count_tag.setText(f"{len(entries)} / {cap}")
        self._count_tag.setVisible(bool(entries))
        # Nothing to copy/clear on an empty list — disabled (exercises the
        # dialog's :disabled styling, and reads correctly).
        self._copy_all_btn.setEnabled(bool(entries))
        self._clear_btn.setEnabled(bool(entries))
        self._empty_label.setVisible(not entries)
        self._list.setVisible(bool(entries))

    def _on_clear_clicked(self) -> None:
        history.clear()
        self.refresh()
        self.history_cleared.emit()

    # ------------------------------------------------------------- copy
    def _copy_text(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        QApplication.clipboard().setText(text)
        QToolTip.showText(QCursor.pos(), "Copied ✓", self._list)

    def _copy_item(self, item: QListWidgetItem | None) -> None:
        if item is not None:
            self._copy_text(item.data(_TEXT_ROLE))

    def _copy_selected(self) -> None:
        self._copy_item(self._list.currentItem())

    def _on_copy_all(self) -> None:
        entries = history.load_all()  # oldest-first — reads as a chronological log
        if not entries:
            return
        blob = "\n".join(f"[{_format_ts(e.ts)}] {e.text}" for e in entries)
        QApplication.clipboard().setText(blob)
        QToolTip.showText(
            QCursor.pos(), f"Copied {len(entries)} transcripts ✓", self._copy_all_btn
        )

    def _show_context_menu(self, pos) -> None:
        menu = QMenu(self._list)
        item = self._list.itemAt(pos)
        if item is not None:
            menu.addAction("Copy", lambda: self._copy_item(item))
        menu.addAction("Copy all", self._on_copy_all)
        menu.exec(self._list.mapToGlobal(pos))


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
