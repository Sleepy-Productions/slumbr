"""Shared widget helpers for Settings tabs.

The old ``hub_panels.py`` defined these privately at module level —
extracting them here so each tab module stays tight and only contains
its own page logic.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QLabel,
    QLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...theme import (
    BG_DARK,
    BG_PANEL,
    BG_PANEL_HI,
    BORDER,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)


class NoScrollComboBox(QComboBox):
    """A combo box that never hijacks the scroll wheel.

    Stock ``QComboBox`` grabs wheel events whenever the cursor is over it and
    changes the selection — so scrolling a long settings page lands on a
    dropdown and silently edits it (the exact annoyance the user hit). Here,
    the wheel only changes the value when the combo actually has keyboard
    focus (i.e. you clicked into it). Otherwise the event is ignored and
    bubbles up to the enclosing scroll area, so the page scrolls as expected.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Take focus on click / Tab, but NOT from the wheel — that's what
        # lets an un-focused combo pass the wheel through to the page.
        self.setFocusPolicy(Qt.StrongFocus)

    def wheelEvent(self, event) -> None:  # noqa: N802
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


def section_card(title: str) -> tuple[QFrame, QVBoxLayout]:
    """A titled card that groups related settings — gives a tab clear visual
    structure instead of one flat wall of controls. Returns the card frame
    (add it to the page) and the inner layout (add the controls to it)."""
    card = QFrame()
    card.setObjectName("sectionCard")
    card.setStyleSheet(
        f"QFrame#sectionCard {{ background: {BG_PANEL}; border: 1px solid {BORDER}; "
        f"border-radius: 12px; }}"
    )
    v = QVBoxLayout(card)
    v.setContentsMargins(20, 14, 20, 16)
    v.setSpacing(10)
    hdr = QLabel(title)
    hf = QFont()
    hf.setPointSize(12)
    hf.setBold(True)
    hdr.setFont(hf)
    hdr.setStyleSheet(f"color: {TEXT_PRIMARY};")
    v.addWidget(hdr)
    return card, v


def keycap(text: str) -> QLabel:
    """A keyboard-key-style badge — monospace, with a thicker bottom border
    so it reads as a physical key. Used to display key / combo names as chips
    instead of a run-on, ·-separated sentence."""
    lbl = QLabel(text)
    f = QFont("Consolas")
    f.setPointSize(9)
    f.setBold(True)
    lbl.setFont(f)
    lbl.setStyleSheet(
        f"color: {TEXT_PRIMARY}; background: {BG_PANEL_HI}; "
        f"border: 1px solid {BORDER}; border-bottom: 2px solid {BORDER}; "
        "border-radius: 6px; padding: 4px 9px;"
    )
    return lbl


def tag(text: str) -> QLabel:
    """A soft rounded pill for short informational labels (feature badges)."""
    lbl = QLabel(text)
    f = QFont()
    f.setPointSize(9)
    f.setBold(True)
    lbl.setFont(f)
    lbl.setStyleSheet(
        f"color: {TEXT_SECONDARY}; background: {BG_PANEL_HI}; "
        f"border: 1px solid {BORDER}; border-radius: 11px; padding: 5px 12px;"
    )
    return lbl


class FlowLayout(QLayout):
    """Left-to-right layout that wraps to the next row when it runs out of
    width — for chips / badges that should reflow with the panel width
    instead of clipping or forcing a fixed width. (Standard Qt FlowLayout.)"""

    def __init__(self, parent: QWidget | None = None, spacing: int = 8) -> None:
        super().__init__(parent)
        self._spacing = spacing
        self._items: list = []
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i: int):  # noqa: N802
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i: int):  # noqa: N802
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        x, y, line_height = rect.x(), rect.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + self._spacing
            if next_x - self._spacing > rect.right() and line_height > 0:
                x = rect.x()
                y = y + line_height + self._spacing
                next_x = x + hint.width() + self._spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y()


def heading(text: str, size: int = 22, bold: bool = True) -> QLabel:
    lbl = QLabel(text)
    f = QFont()
    f.setPointSize(size)
    f.setBold(bold)
    lbl.setFont(f)
    return lbl


def subheading(text: str) -> QLabel:
    lbl = QLabel(text)
    f = QFont()
    f.setPointSize(10)
    lbl.setFont(f)
    lbl.setStyleSheet(f"color: {TEXT_SECONDARY};")
    lbl.setWordWrap(True)
    return lbl


def field_label(text: str) -> QLabel:
    lbl = QLabel(text)
    f = QFont()
    f.setPointSize(10)
    f.setBold(True)
    lbl.setFont(f)
    lbl.setStyleSheet(f"color: {TEXT_PRIMARY};")
    return lbl


def field_hint(text: str) -> QLabel:
    lbl = QLabel(text)
    f = QFont()
    f.setPointSize(9)
    lbl.setFont(f)
    lbl.setStyleSheet(f"color: {TEXT_SECONDARY};")
    lbl.setWordWrap(True)
    return lbl


def scrollable(content: QWidget) -> QScrollArea:
    sc = QScrollArea()
    sc.setWidget(content)
    sc.setWidgetResizable(True)
    sc.setFrameShape(QFrame.NoFrame)
    sc.setStyleSheet(f"QScrollArea {{ background: {BG_DARK}; border: none; }}")
    return sc
