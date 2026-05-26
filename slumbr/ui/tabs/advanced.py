"""Advanced tab — power-user knobs, tucked out of the way.

Slumbr works out of the box, so the fiddly bits live here: the virtual-cable
device + installer, paste extras (auto-send, keep-on-clipboard), and the
vocabulary hint. Most users never need to open this tab.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...audio.mirror import find_virtual_cables
from ...bootstrap.vbcable import VBCableInstallWorker
from ...config import SlumbrConfig
from ...theme import (
    BG_PANEL,
    BORDER,
    FONT_DISPLAY,
    RADIUS_MD,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from ._widgets import (
    NoScrollComboBox,
    field_hint,
    field_label,
    heading,
    scrollable,
    section_card,
    subheading,
)


class AdvancedTab(QWidget):
    config_changed = Signal()

    def __init__(self, config: SlumbrConfig) -> None:
        super().__init__()
        self._config = config
        self._status_dot: QLabel | None = None  # accent-colored when a cable is found

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(40, 28, 40, 28)
        layout.setSpacing(16)

        layout.addWidget(heading("Advanced", size=28))
        layout.addWidget(
            subheading(
                "Power-user knobs. Slumbr works out of the box without touching any "
                "of this — change it only if you want to."
            )
        )

        # ===== Virtual cable =====
        _card, sl = section_card("Virtual mic cable")
        self._cables = find_virtual_cables()
        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        status_dot = QLabel("●")
        if self._cables:
            status_dot.setStyleSheet(f"color: {config.accent_color}; font-size: 14px;")
            self._status_dot = status_dot
            status_text = QLabel(
                f"Detected {len(self._cables)} usable virtual cable"
                + ("s" if len(self._cables) > 1 else "")
                + '. Set your call app\'s mic to "CABLE Output".'
            )
        else:
            status_dot.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 14px;")
            status_text = QLabel(
                "No virtual cable detected. Slumbr can install VB-Cable for you "
                "(downloads from vb-audio.com, requires admin + reboot)."
            )
        status_text.setWordWrap(True)
        status_row.addWidget(status_dot)
        status_row.addWidget(status_text, stretch=1)
        sl.addLayout(status_row)

        if not self._cables:
            self._install_btn = QPushButton("Install VB-Cable")
            self._install_btn.setObjectName("primary")
            self._install_btn.setMaximumWidth(220)
            self._install_btn.clicked.connect(self._on_install_vbcable)
            sl.addWidget(self._install_btn)

        cable_row = QHBoxLayout()
        cable_row.setSpacing(10)
        cable_row.setContentsMargins(0, 4, 0, 0)
        cable_row.addWidget(field_label("Virtual cable:"))
        self._cable_combo = NoScrollComboBox()
        self._cable_combo.addItem("(none)", userData="")
        for _idx, name in self._cables:
            self._cable_combo.addItem(name, userData=name)
        # Restore the configured cable if it still exists, else auto-pick the
        # first one AND write it to config — without the write, ticking the
        # routing toggle (Behavior tab) would leave the device empty and the
        # mirror would silently never open.
        if config.mic_routing_device_name:
            i = self._cable_combo.findData(config.mic_routing_device_name)
            if i >= 0:
                self._cable_combo.setCurrentIndex(i)
        elif self._cables:
            self._cable_combo.setCurrentIndex(1)  # index 0 is "(none)"
            self._config.mic_routing_device_name = self._cables[0][1]
        self._cable_combo.currentIndexChanged.connect(self._on_cable_changed)
        self._cable_combo.setEnabled(bool(self._cables))
        cable_row.addWidget(self._cable_combo, stretch=1)
        sl.addLayout(cable_row)
        layout.addWidget(_card)

        # ===== Pasting extras =====
        _card, sl = section_card("Pasting")
        self._auto_send_cb = QCheckBox(
            "Auto-send — press Enter right after your second hotkey tap"
        )
        self._auto_send_cb.setChecked(config.auto_send)
        self._auto_send_cb.toggled.connect(self._on_changed)
        sl.addWidget(self._auto_send_cb)
        sl.addWidget(
            field_hint(
                "Tip: click into the box you want before your second tap, so it "
                "pastes (and sends) in the right place."
            )
        )
        self._keep_clip_cb = QCheckBox(
            "Keep the transcript on your clipboard (paste it again anywhere)"
        )
        self._keep_clip_cb.setChecked(config.keep_transcript_on_clipboard)
        self._keep_clip_cb.toggled.connect(self._on_changed)
        sl.addWidget(self._keep_clip_cb)
        sl.addWidget(
            field_hint(
                "Heads up: each new dictation overwrites your clipboard with the "
                "latest transcript."
            )
        )
        layout.addWidget(_card)

        # ===== Vocabulary =====
        _card, sl = section_card("Vocabulary")
        sl.addWidget(
            field_hint(
                "List proper nouns, technical terms, slang — anything Slumbr "
                "mishears. Up to ~200 tokens. Biases the Whisper backends "
                "(Moonshine ignores this)."
            )
        )
        self._prompt_edit = QPlainTextEdit()
        self._prompt_edit.setPlainText(config.initial_prompt)
        self._prompt_edit.setPlaceholderText(
            "Slumbr, Sleepy Productions, PySide6, faster-whisper, sherpa-onnx..."
        )
        self._prompt_edit.setFixedHeight(110)
        self._prompt_edit.textChanged.connect(self._on_changed)
        sl.addWidget(self._prompt_edit)
        layout.addWidget(_card)

        layout.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scrollable(body))

    # ----------------------------------------------------------- handlers
    def _on_changed(self, *_args) -> None:
        self._config.auto_send = self._auto_send_cb.isChecked()
        self._config.keep_transcript_on_clipboard = self._keep_clip_cb.isChecked()
        self._config.initial_prompt = self._prompt_edit.toPlainText().strip()
        self.config_changed.emit()

    def _on_cable_changed(self, *_args) -> None:
        self._config.mic_routing_device_name = self._cable_combo.currentData() or ""
        self.config_changed.emit()

    def _on_install_vbcable(self) -> None:
        """Open the install dialog and kick off the VB-Cable installer."""
        _VBCableInstallDialog(self).exec()

    def reflect_accent(self, primary: str) -> None:
        """Recolor the virtual-cable status dot to the accent (when shown)."""
        if self._status_dot is not None:
            self._status_dot.setStyleSheet(f"color: {primary}; font-size: 14px;")


class _VBCableInstallDialog(QDialog):
    """Modal progress dialog that runs the VB-Cable installer worker."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Install VB-Cable")
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setMinimumSize(560, 380)
        # Inherit the parent's stylesheet so it matches the Settings dialog.
        self.setStyleSheet(parent.styleSheet() if parent else "")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 26, 28, 24)
        outer.setSpacing(14)

        title = QLabel("Installing VB-Cable…")
        tf = title.font()
        tf.setFamily(FONT_DISPLAY)
        tf.setPointSize(15)
        tf.setBold(True)
        title.setFont(tf)
        self._title = title
        outer.addWidget(title)

        self._subtitle = QLabel(
            "Slumbr downloads the official installer from vb-audio.com and runs "
            "it elevated. Windows will prompt for admin. Click 'Install Driver' "
            "in the VB-Cable installer when it appears."
        )
        self._subtitle.setWordWrap(True)
        self._subtitle.setStyleSheet(f"color: {TEXT_SECONDARY};")
        outer.addWidget(self._subtitle)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setStyleSheet(
            f"QPlainTextEdit {{ background: {BG_PANEL}; border: 1px solid {BORDER}; "
            f"border-radius: {RADIUS_MD}px; padding: 10px; color: {TEXT_PRIMARY}; "
            "font-family: Consolas; font-size: 9pt; }}"
        )
        outer.addWidget(self._log, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._close_btn = QPushButton("Close")
        self._close_btn.clicked.connect(self.accept)
        self._close_btn.setEnabled(False)
        btn_row.addWidget(self._close_btn)
        outer.addLayout(btn_row)

        self._thread = QThread(self)
        self._worker = VBCableInstallWorker()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_line)
        self._worker.finished.connect(self._on_finished)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _on_line(self, msg: str) -> None:
        self._log.appendPlainText(msg)
        bar = self._log.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _on_finished(self, success: bool, summary: str) -> None:
        if success:
            self._title.setText("VB-Cable installed")
            self._subtitle.setText(
                "Reboot Windows now, then re-launch Slumbr. The new cable will "
                "show up automatically in Settings → Advanced."
            )
            self._log.appendPlainText("")
            self._log.appendPlainText("[ok] " + summary)
        else:
            self._title.setText("Install failed")
            self._subtitle.setText(
                "Something didn't work. You can retry, or install manually from "
                "https://vb-audio.com/Cable/."
            )
            self._log.appendPlainText("")
            self._log.appendPlainText("[fail] " + summary)
        self._close_btn.setEnabled(True)
        self._close_btn.setText("Done")
