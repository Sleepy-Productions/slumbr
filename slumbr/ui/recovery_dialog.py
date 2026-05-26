"""Crash-recovery prompt.

Shown on launch when the previous session left its running marker behind (it
didn't close cleanly). Offers to restore that session's transcripts — the
rolled session-log batches + the live partial — or discard them. A durable
crash log is written regardless, so Discard never loses data silently.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ..theme import (
    BG_DARK,
    BG_PANEL_HI,
    BORDER,
    FONT_BODY,
    FONT_DISPLAY,
    RADIUS_MD,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    derive_accent,
    text_on,
)


def _qss(primary: str, hover: str, deep: str) -> str:
    on_primary = text_on(primary)
    return f"""
    QDialog {{ background-color: {BG_DARK}; }}
    QWidget {{ color: {TEXT_PRIMARY}; font-family: "{FONT_BODY}", "Segoe UI"; }}
    QLabel {{ color: {TEXT_PRIMARY}; }}
    QPushButton {{
        background-color: {BG_PANEL_HI};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER};
        border-radius: {RADIUS_MD}px;
        padding: 10px 18px;
        font-weight: 500;
    }}
    QPushButton:hover {{ border: 1px solid {primary}; }}
    QPushButton#primary {{
        background-color: {primary};
        color: {on_primary};
        border: 1px solid {primary};
        font-weight: 700;
    }}
    QPushButton#primary:hover {{
        background-color: {hover};
        border: 1px solid {hover};
    }}
    QPushButton#destructive:hover {{ border: 1px solid #C97A7A; color: #FFCDCD; }}
    """


class RecoveryDialog(QDialog):
    """Modal Recover/Discard prompt. ``exec()`` returns ``QDialog.Accepted``
    for Recover, ``Rejected`` for Discard (or close)."""

    def __init__(self, count: int, accent: str, app_icon: QIcon | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Slumbr — Recover transcripts")
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        if app_icon is not None:
            self.setWindowIcon(app_icon)
        primary, hover, deep, _pill = derive_accent(accent)
        self.setStyleSheet(_qss(primary, hover, deep))
        self.setMinimumWidth(460)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 22)
        layout.setSpacing(14)

        title = QLabel("Recover your last session?")
        tf = QFont(FONT_DISPLAY)
        tf.setPointSize(17)
        tf.setBold(True)
        title.setFont(tf)
        layout.addWidget(title)

        plural = "s" if count != 1 else ""
        body = QLabel(
            f"Slumbr didn't close properly last time. {count} transcript{plural} "
            f"from that session can be restored to your History and Session logs.\n\n"
            "Either way, a crash log was saved to your Slumbr folder — so nothing "
            "is lost without your say-so."
        )
        body.setWordWrap(True)
        bf = QFont()
        bf.setPointSize(11)
        body.setFont(bf)
        body.setStyleSheet(f"color: {TEXT_SECONDARY};")
        layout.addWidget(body)

        layout.addSpacing(4)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_row.addStretch(1)
        discard = QPushButton("Discard")
        discard.setObjectName("destructive")
        discard.setMinimumHeight(40)
        discard.clicked.connect(self.reject)
        btn_row.addWidget(discard)
        recover = QPushButton("Recover")
        recover.setObjectName("primary")
        recover.setMinimumHeight(40)
        recover.setMinimumWidth(130)
        recover.setDefault(True)
        recover.clicked.connect(self.accept)
        btn_row.addWidget(recover)
        layout.addLayout(btn_row)

        # Quiet footnote naming the folder, since we reference "your Slumbr folder".
        hint = QLabel("Crash logs live in  %APPDATA%\\Slumbr\\crash-logs")
        hf = QFont(FONT_BODY)
        hf.setPointSize(8)
        hint.setFont(hf)
        hint.setStyleSheet(f"color: {TEXT_SECONDARY}; padding-top: 4px;")
        layout.addWidget(hint)
