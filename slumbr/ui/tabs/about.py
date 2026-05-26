"""About tab — version, branding, repo link, restart + quit."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ... import __version__
from ...theme import TEXT_PRIMARY, TEXT_SECONDARY, VIOLET_PRIMARY
from ._widgets import heading, scrollable

_REPO_URL = "https://github.com/SIeepyDev/slumbr"


class AboutTab(QWidget):
    quit_requested = Signal()
    restart_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(56, 52, 56, 52)
        layout.setSpacing(16)

        layout.addWidget(heading("About Slumbr", size=32))

        brand = QLabel("Sleepy Productions")
        brand.setStyleSheet(f"color: {VIOLET_PRIMARY}; font-weight: 700;")
        bf = QFont()
        bf.setPointSize(17)
        brand.setFont(bf)
        layout.addWidget(brand)

        version = QLabel(f"Version {__version__}")
        vf = QFont()
        vf.setPointSize(12)
        version.setFont(vf)
        version.setStyleSheet(f"color: {TEXT_SECONDARY};")
        layout.addWidget(version)

        tagline = QLabel(
            "Local, offline voice dictation for Windows. Fully on-device — "
            "no accounts, no cloud, no telemetry. Your voice never leaves "
            "the machine."
        )
        tagline.setWordWrap(True)
        tf = QFont()
        tf.setPointSize(12)
        tagline.setFont(tf)
        tagline.setStyleSheet(f"color: {TEXT_PRIMARY}; padding-top: 8px;")
        layout.addWidget(tagline)

        link = QLabel(
            f'<a href="{_REPO_URL}" style="color: {VIOLET_PRIMARY};">{_REPO_URL}</a>'
        )
        lf = QFont()
        lf.setPointSize(11)
        link.setFont(lf)
        link.setOpenExternalLinks(True)
        link.setTextInteractionFlags(Qt.TextBrowserInteraction)
        link.setStyleSheet("padding-top: 4px;")
        layout.addWidget(link)

        license_label = QLabel("Released under the MIT License.")
        license_label.setStyleSheet(f"color: {TEXT_SECONDARY};")
        layout.addWidget(license_label)

        layout.addSpacing(28)

        # Restart + Quit, side by side. Restart is the primary action
        # (applies pending Engine/model changes); Quit is destructive.
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        restart_btn = QPushButton("Restart Slumbr")
        restart_btn.setObjectName("primary")
        restart_btn.setMinimumHeight(40)
        restart_btn.setMinimumWidth(150)
        restart_btn.clicked.connect(self.restart_requested.emit)
        btn_row.addWidget(restart_btn)

        quit_btn = QPushButton("Quit Slumbr")
        quit_btn.setObjectName("destructive")
        quit_btn.setMinimumHeight(40)
        quit_btn.setMinimumWidth(150)
        quit_btn.clicked.connect(self.quit_requested.emit)
        btn_row.addWidget(quit_btn)

        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        restart_hint = QLabel(
            "Restart applies pending Engine / model changes and reloads the app."
        )
        restart_hint.setWordWrap(True)
        restart_hint.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9pt; padding-top: 4px;")
        layout.addWidget(restart_hint)

        layout.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scrollable(body))
