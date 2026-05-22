"""About tab — version, branding, repo link, quit."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from ... import __version__
from ...theme import TEXT_PRIMARY, TEXT_SECONDARY, VIOLET_PRIMARY
from ._widgets import heading, scrollable

_REPO_URL = "https://github.com/SIeepyDev/slumbr"


class AboutTab(QWidget):
    quit_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(56, 48, 56, 48)
        layout.setSpacing(22)

        layout.addWidget(heading("About Slumbr", size=28))

        brand = QLabel("Sleepy Productions")
        brand.setStyleSheet(f"color: {VIOLET_PRIMARY}; font-weight: 700;")
        bf = QFont()
        bf.setPointSize(13)
        brand.setFont(bf)
        layout.addWidget(brand)

        version = QLabel(f"Version {__version__}")
        version.setStyleSheet(f"color: {TEXT_SECONDARY};")
        layout.addWidget(version)

        tagline = QLabel(
            "Local, offline voice dictation for Windows. Fully on-device — "
            "no accounts, no cloud, no telemetry."
        )
        tagline.setWordWrap(True)
        tagline.setStyleSheet(f"color: {TEXT_PRIMARY}; padding-top: 6px;")
        layout.addWidget(tagline)

        link = QLabel(
            f'<a href="{_REPO_URL}" style="color: {VIOLET_PRIMARY};">{_REPO_URL}</a>'
        )
        link.setOpenExternalLinks(True)
        link.setTextInteractionFlags(Qt.TextBrowserInteraction)
        layout.addWidget(link)

        license_label = QLabel("Released under the MIT License.")
        license_label.setStyleSheet(f"color: {TEXT_SECONDARY};")
        layout.addWidget(license_label)

        layout.addSpacing(20)
        quit_btn = QPushButton("Quit Slumbr")
        quit_btn.setObjectName("destructive")
        quit_btn.setMinimumHeight(36)
        quit_btn.setMaximumWidth(160)
        quit_btn.clicked.connect(self.quit_requested.emit)
        layout.addWidget(quit_btn)

        layout.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scrollable(body))
