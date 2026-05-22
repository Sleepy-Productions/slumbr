"""Shared widget helpers for Settings tabs.

The old ``hub_panels.py`` defined these privately at module level —
extracting them here so each tab module stays tight and only contains
its own page logic.
"""

from __future__ import annotations

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame, QLabel, QScrollArea, QWidget

from ...theme import BG_DARK, TEXT_PRIMARY, TEXT_SECONDARY


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
