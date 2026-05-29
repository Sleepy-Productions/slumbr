"""History tab — this session's recent transcripts (in-memory, ephemeral).

A single live list, newest-first, holding at most ``history.MAX_ENTRIES``. When
it fills it clears and starts fresh; nothing is written to disk and it's gone
when Slumbr closes. Copy any line (double-click / right-click / Ctrl+C) or
"Copy all". No session logs, no recovery — by design.
"""

from __future__ import annotations

import time
from datetime import datetime

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
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
from ...config import SlumbrConfig
from ...theme import BG_PANEL, BG_PANEL_HI, BORDER, TEXT_PRIMARY, TEXT_SECONDARY
from ._widgets import field_hint, heading, subheading, tag

# Per-item data roles: the delegate paints the timestamp + transcript in two
# different styles, so we stash them separately instead of one display string.
_TS_ROLE = Qt.UserRole + 1
_TEXT_ROLE = Qt.UserRole + 2

_LIST_QSS = f"""
QListWidget {{
    background: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 12px;
    padding: 8px;
    outline: 0;
}}
"""


class _HistoryDelegate(QStyledItemDelegate):
    """Paints each transcript row as a dim monospace timestamp + the transcript
    in primary text, elided to the row width."""

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
        elided = QFontMetrics(body_font).elidedText(text, Qt.ElideRight, body_rect.width())
        painter.drawText(body_rect, Qt.AlignLeft | Qt.AlignVCenter, elided)
        painter.restore()

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        return QSize(0, self.ROW_H)


class HistoryTab(QWidget):
    history_cleared = Signal()
    config_changed = Signal()

    def __init__(self, cfg: SlumbrConfig) -> None:
        super().__init__()
        self._cfg = cfg
        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 40, 48, 40)
        layout.setSpacing(20)

        header_row = QHBoxLayout()
        header_row.setSpacing(12)
        header_row.addWidget(heading("History", size=28))
        header_row.addStretch(1)
        self._count_tag = tag("0")
        header_row.addWidget(self._count_tag)
        self._copy_all_btn = QPushButton("Copy all")
        self._copy_all_btn.clicked.connect(lambda: self._copy_all_from(self._live_list))
        header_row.addWidget(self._copy_all_btn)
        self._clear_btn = QPushButton("Clear history")
        self._clear_btn.setObjectName("destructive")
        self._clear_btn.clicked.connect(self._on_clear_clicked)
        header_row.addWidget(self._clear_btn)
        layout.addLayout(header_row)

        layout.addWidget(
            subheading(
                "Your most recent dictations, newest first — a rolling list of "
                f"the latest {getattr(history, 'MAX_ENTRIES', 200)}. Double-click, "
                "right-click, or select + Ctrl+C to copy a line; Copy all grabs "
                "them together."
            )
        )

        # Opt-in persistence. Off by default — the privacy story is in-memory /
        # ephemeral; turning this on saves transcripts to a local file so they
        # survive restarts (and turning it back off deletes that file).
        self._persist_cb = QCheckBox("Keep history across restarts")
        self._persist_cb.setChecked(bool(getattr(self._cfg, "persist_history", False)))
        self._persist_cb.toggled.connect(self._on_persist_toggled)
        layout.addWidget(self._persist_cb)
        layout.addWidget(
            field_hint(
                "Off by default: history lives in memory and is gone when you "
                "close Slumbr. On: transcripts are saved to an unencrypted file "
                "at %APPDATA%\\Slumbr\\history.db so they persist — turning this "
                "back off deletes that file."
            )
        )

        self._live_list = self._make_transcript_list()
        layout.addWidget(self._live_list, stretch=1)

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

    # ----------------------------------------------------------- list helpers

    def _make_transcript_list(self) -> QListWidget:
        lw = QListWidget()
        lw.setItemDelegate(_HistoryDelegate())
        lw.setMouseTracking(True)  # rows highlight on hover
        lw.setStyleSheet(_LIST_QSS)
        # Copy a transcript out: double-click, right-click, or select + Ctrl+C.
        lw.itemDoubleClicked.connect(self._copy_item)
        lw.setContextMenuPolicy(Qt.CustomContextMenu)
        lw.customContextMenuRequested.connect(lambda pos, _l=lw: self._show_context_menu(_l, pos))
        sc = QShortcut(QKeySequence.StandardKey.Copy, lw)
        sc.activated.connect(lambda _l=lw: self._copy_item(_l.currentItem()))
        return lw

    def _populate(self, lw: QListWidget, entries: list) -> None:
        lw.clear()
        for entry in reversed(entries):  # newest first
            item = QListWidgetItem()
            item.setData(_TS_ROLE, _format_ts(entry.ts))
            item.setData(_TEXT_ROLE, entry.text)
            item.setToolTip(entry.text)
            lw.addItem(item)

    # ----------------------------------------------------------- refresh

    def refresh(self) -> None:
        """Re-read the in-memory history. Called when the dialog opens this tab
        + after a clear."""
        entries = history.load_all()
        self._populate(self._live_list, entries)
        cap = getattr(history, "MAX_ENTRIES", 50)
        self._count_tag.setText(f"{len(entries)} / {cap}")
        self._count_tag.setVisible(bool(entries))
        self._copy_all_btn.setEnabled(bool(entries))
        self._clear_btn.setEnabled(bool(entries))
        self._empty_label.setVisible(not entries)
        self._live_list.setVisible(bool(entries))

    def _on_clear_clicked(self) -> None:
        history.clear()
        self.refresh()
        self.history_cleared.emit()

    def _on_persist_toggled(self, checked: bool) -> None:
        """Flip on-disk persistence. Applies immediately (configure backfills the
        live view from the store when turning on, deletes the file when turning
        off), then saves the preference + refreshes the list."""
        self._cfg.persist_history = bool(checked)
        history.configure(self._cfg.persist_history)
        self.refresh()
        self.config_changed.emit()

    # ------------------------------------------------------------- copy
    def _copy_text(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        QApplication.clipboard().setText(text)
        QToolTip.showText(QCursor.pos(), "Copied ✓", self)

    def _copy_item(self, item: QListWidgetItem | None) -> None:
        if item is not None:
            self._copy_text(item.data(_TEXT_ROLE))

    def _copy_all_from(self, lw: QListWidget) -> None:
        rows = [lw.item(i) for i in range(lw.count())]
        if not rows:
            return
        # rows are newest-first in the view; copy oldest-first so it reads as a
        # chronological log.
        blob = "\n".join(f"[{it.data(_TS_ROLE)}] {it.data(_TEXT_ROLE)}" for it in reversed(rows))
        QApplication.clipboard().setText(blob)
        QToolTip.showText(QCursor.pos(), f"Copied {len(rows)} transcripts ✓", self)

    def _show_context_menu(self, lw: QListWidget, pos) -> None:
        menu = QMenu(lw)
        item = lw.itemAt(pos)
        if item is not None:
            menu.addAction("Copy", lambda: self._copy_item(item))
        menu.addAction("Copy all", lambda: self._copy_all_from(lw))
        menu.exec(lw.mapToGlobal(pos))


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
