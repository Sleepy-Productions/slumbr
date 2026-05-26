"""About tab — logo, version, branding, repo link, restart + quit."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ... import __version__
from ...branding import LOGO_COLOR
from ...config import SlumbrConfig
from ...theme import TEXT_PRIMARY, TEXT_SECONDARY, VIOLET_PRIMARY
from ._widgets import glyph_pixmap, heading, scrollable, tag

_REPO_URL = "https://github.com/SIeepyDev/slumbr"
_LOGO_PX = 88


class AboutTab(QWidget):
    quit_requested = Signal()
    restart_requested = Signal()

    def __init__(self, config: SlumbrConfig) -> None:
        super().__init__()
        self._config = config
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(56, 52, 56, 52)
        layout.setSpacing(14)

        # ===== Header: logo + name + version + brand =====
        header = QHBoxLayout()
        header.setSpacing(20)

        self._logo = QLabel()
        self._logo.setFixedSize(_LOGO_PX, _LOGO_PX)
        self._set_logo(LOGO_COLOR)  # fixed monochrome brand mark, not the accent
        header.addWidget(self._logo, 0, Qt.AlignTop)

        namecol = QVBoxLayout()
        namecol.setSpacing(6)

        name_row = QHBoxLayout()
        name_row.setSpacing(12)
        name = heading("Slumbr", size=32)
        name_row.addWidget(name, 0, Qt.AlignVCenter)
        self._version_pill = tag(f"v{__version__}")
        name_row.addWidget(self._version_pill, 0, Qt.AlignVCenter)
        name_row.addStretch(1)
        namecol.addLayout(name_row)

        self._brand = QLabel("Sleepy Productions")
        self._brand.setStyleSheet(f"color: {VIOLET_PRIMARY}; font-weight: 700;")
        bf = QFont()
        bf.setPointSize(15)
        self._brand.setFont(bf)
        namecol.addWidget(self._brand)
        namecol.addStretch(1)

        header.addLayout(namecol, 1)
        layout.addLayout(header)

        # ===== Tagline =====
        tagline = QLabel(
            "Local, offline voice dictation for Windows. Fully on-device — "
            "no accounts, no cloud, no telemetry. Your voice never leaves "
            "the machine."
        )
        tagline.setWordWrap(True)
        tf = QFont()
        tf.setPointSize(12)
        tagline.setFont(tf)
        tagline.setStyleSheet(f"color: {TEXT_PRIMARY}; padding-top: 6px;")
        layout.addWidget(tagline)

        # ===== Feature chips =====
        chips = QHBoxLayout()
        chips.setSpacing(8)
        chips.setContentsMargins(0, 6, 0, 0)
        for label in ("Offline", "On-device", "No telemetry", "Open source · MIT"):
            chips.addWidget(tag(label))
        chips.addStretch(1)
        layout.addLayout(chips)

        # ===== Repo link =====
        self._link = QLabel(
            f'<a href="{_REPO_URL}" style="color: {VIOLET_PRIMARY};">{_REPO_URL}</a>'
        )
        lf = QFont()
        lf.setPointSize(11)
        self._link.setFont(lf)
        self._link.setOpenExternalLinks(True)
        self._link.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self._link.setStyleSheet("padding-top: 10px;")
        layout.addWidget(self._link)

        layout.addSpacing(24)

        # ===== Restart + Quit =====
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
        restart_hint.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 9pt; padding-top: 4px;"
        )
        layout.addWidget(restart_hint)

        layout.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scrollable(body))

    def _set_logo(self, color: str) -> None:
        """Render the moon-v2 brand mark (rendered 2× then scaled down for crisp
        edges). Best-effort — never block the tab on art."""
        try:
            pm = glyph_pixmap(color, _LOGO_PX * 2).scaled(
                _LOGO_PX, _LOGO_PX, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self._logo.setPixmap(pm)
        except Exception:  # noqa: BLE001
            pass

    def reflect_accent(self, primary: str) -> None:
        """Recolor the brand text + repo link to the accent. The logo stays a
        fixed monochrome mark — it's the brand symbol, not an accent surface."""
        self._brand.setStyleSheet(f"color: {primary}; font-weight: 700;")
        self._link.setText(
            f'<a href="{_REPO_URL}" style="color: {primary};">{_REPO_URL}</a>'
        )
