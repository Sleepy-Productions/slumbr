"""Shared widget helpers for Settings tabs.

The old ``hub_panels.py`` defined these privately at module level —
extracting them here so each tab module stays tight and only contains
its own page logic.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...theme import BG_DARK, BG_PANEL, BORDER, TEXT_PRIMARY, TEXT_SECONDARY


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
