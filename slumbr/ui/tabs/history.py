"""History tab — the live transcript batch + a drill-in for session logs.

Three views in one tab (a QStackedWidget), so the fallback never feels clunky:

  1. Live    — this session's current batch (< ``history.MAX_ENTRIES``). At the
               cap it rolls into a session log and resets to fresh.
  2. Logs    — "Session logs (N)": the rolled-off batches, one row each. They're
               temporary (deleted when Slumbr closes) and exist as a safety net
               for "the list auto-cleared and I needed that one".
  3. Detail  — one log's transcripts, opened from the Logs list (one at a time,
               not all dumped at once).

Live entries live at ``%APPDATA%\\Slumbr\\history.jsonl``; rolled batches under
``%APPDATA%\\Slumbr\\session\\`` (see ``slumbr/history.py`` + ``session_logs.py``).
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
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from ... import history, session_logs
from ...theme import BG_PANEL, BG_PANEL_HI, BORDER, TEXT_PRIMARY, TEXT_SECONDARY
from ._widgets import heading, subheading, tag

# Per-item data roles: the delegate paints the timestamp + transcript in two
# different styles, so we stash them separately instead of one display string.
_TS_ROLE = Qt.UserRole + 1
_TEXT_ROLE = Qt.UserRole + 2
_INDEX_ROLE = Qt.UserRole + 3  # batch index, on Session-logs rows

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
    in primary text, elided to the row width. Reads cleaner than one flat
    ``"13:42   text"`` string and never wraps oddly."""

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

    def __init__(self) -> None:
        super().__init__()
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_live_page())  # 0
        self._stack.addWidget(self._build_logs_page())  # 1
        self._stack.addWidget(self._build_detail_page())  # 2

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._stack)

        self.refresh()

    # ----------------------------------------------------------- page builds

    def _page(self) -> tuple[QWidget, QVBoxLayout]:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(48, 40, 48, 40)
        lay.setSpacing(20)
        return w, lay

    def _build_live_page(self) -> QWidget:
        page, layout = self._page()

        header_row = QHBoxLayout()
        header_row.setSpacing(12)
        header_row.addWidget(heading("History", size=28))
        header_row.addStretch(1)
        self._count_tag = tag("0")
        header_row.addWidget(self._count_tag)
        self._logs_btn = QPushButton("Session logs")
        self._logs_btn.setToolTip(
            "Earlier transcripts that rolled off this session.\n"
            "Temporary — kept until you close Slumbr."
        )
        self._logs_btn.clicked.connect(self._show_logs)
        header_row.addWidget(self._logs_btn)
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
                "Your live transcripts this session. At 30 the batch rolls into "
                "Session logs and the list starts fresh. Local only, never sent "
                "anywhere. Double-click, right-click, or select + Ctrl+C to copy."
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

        return page

    def _build_logs_page(self) -> QWidget:
        page, layout = self._page()

        header_row = QHBoxLayout()
        header_row.setSpacing(12)
        back = QPushButton("‹  History")
        back.clicked.connect(lambda: self._stack.setCurrentIndex(0))
        header_row.addWidget(back)
        header_row.addWidget(heading("Session logs", size=28))
        header_row.addStretch(1)
        self._logs_count_tag = tag("0")
        header_row.addWidget(self._logs_count_tag)
        layout.addLayout(header_row)

        layout.addWidget(
            subheading(
                "Batches that rolled off your live History this session — your "
                "fallback if the list auto-cleared and you needed one back. "
                "Temporary: these are deleted when you close Slumbr. "
                "Click a log to view its transcripts."
            )
        )

        self._logs_list = QListWidget()
        self._logs_list.setStyleSheet(_LIST_QSS)
        self._logs_list.setMouseTracking(True)
        self._logs_list.itemActivated.connect(self._open_batch_item)
        self._logs_list.itemClicked.connect(self._open_batch_item)
        layout.addWidget(self._logs_list, stretch=1)

        return page

    def _build_detail_page(self) -> QWidget:
        page, layout = self._page()

        header_row = QHBoxLayout()
        header_row.setSpacing(12)
        back = QPushButton("‹  Session logs")
        back.clicked.connect(self._show_logs)
        header_row.addWidget(back)
        self._detail_title = heading("Log", size=28)
        header_row.addWidget(self._detail_title)
        header_row.addStretch(1)
        self._detail_count_tag = tag("0")
        header_row.addWidget(self._detail_count_tag)
        detail_copy = QPushButton("Copy all")
        detail_copy.clicked.connect(lambda: self._copy_all_from(self._detail_list))
        header_row.addWidget(detail_copy)
        layout.addLayout(header_row)

        self._detail_list = self._make_transcript_list()
        layout.addWidget(self._detail_list, stretch=1)

        return page

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

    # ----------------------------------------------------------- refresh / nav

    def refresh(self) -> None:
        """Re-read the live batch from disk + update the Session-logs button.
        Called when the dialog opens this tab + after a clear."""
        entries = history.load_all()
        self._populate(self._live_list, entries)
        cap = getattr(history, "MAX_ENTRIES", 30)
        self._count_tag.setText(f"{len(entries)} / {cap}")
        self._count_tag.setVisible(bool(entries))
        self._copy_all_btn.setEnabled(bool(entries))
        self._clear_btn.setEnabled(bool(entries))
        self._empty_label.setVisible(not entries)
        self._live_list.setVisible(bool(entries))

        n_batches = len(session_logs.list_batches())
        self._logs_btn.setText(f"Session logs ({n_batches})")
        self._logs_btn.setVisible(n_batches > 0)

        # If a batch was cleared out from under us, don't strand the user deep
        # in the browser.
        if n_batches == 0 and self._stack.currentIndex() != 0:
            self._stack.setCurrentIndex(0)

    def _show_logs(self) -> None:
        batches = session_logs.list_batches()
        self._logs_list.clear()
        for meta in reversed(batches):  # newest log first
            item = QListWidgetItem(
                f"Log {meta.index}    ·    {meta.count} transcripts    ·    "
                f"{_format_clock(meta.first_ts)}–{_format_clock(meta.last_ts)}"
            )
            f = QFont()
            f.setPointSize(11)
            item.setFont(f)
            item.setSizeHint(QSize(0, 46))
            item.setData(_INDEX_ROLE, meta.index)
            self._logs_list.addItem(item)
        self._logs_count_tag.setText(str(len(batches)))
        self._stack.setCurrentIndex(1)

    def _open_batch_item(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        idx = item.data(_INDEX_ROLE)
        if idx is None:
            return
        entries = session_logs.load_batch(int(idx))
        self._populate(self._detail_list, entries)
        self._detail_title.setText(f"Log {idx}")
        self._detail_count_tag.setText(str(len(entries)))
        self._stack.setCurrentIndex(2)

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


def _format_clock(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")
