"""Behavior tab — paste method, auto-send, clipboard preservation,
reverse-PTT.

The old ``BehaviorPanel`` also owned the close-to-tray toggle; that
field is dead in the post-rearch UI (no hub window to close) but the
field remains on ``SlumbrConfig`` for backwards-compat. We don't
expose it here.

Reverse-PTT (added Phase 2C-ish, prototype period): when enabled +
configured, Slumbr presses a user-chosen key while dictating, so the
other app's own push-to-mute keybind silences the mic externally
while Slumbr keeps capturing internally. Discord is the canonical
target — set Discord's "Push To Mute" keybind to e.g. F23, then put
F23 here. The proper universal version (via VB-Cable) is a Phase 3
backlog item.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QThread, Signal
from PySide6.QtGui import QFont, QKeyEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
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
from ...input.keymap import vk_label
from ...theme import (
    BG_PANEL,
    BORDER,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    VIOLET_PRIMARY,
)
from ._widgets import field_hint, field_label, heading, scrollable, subheading


class BehaviorTab(QWidget):
    config_changed = Signal()

    def __init__(self, config: SlumbrConfig) -> None:
        super().__init__()
        self._config = config

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(40, 28, 40, 28)
        layout.setSpacing(16)

        layout.addWidget(heading("Behavior", size=28))
        layout.addWidget(
            subheading(
                "How Slumbr inserts the transcript into the focused window, "
                "and how it plays nice with calls."
            )
        )

        # ===== Section: Pasting =====
        _card, sl = self._section("Pasting")
        sl.addWidget(field_label("Paste method"))
        self._paste_combo = QComboBox()
        self._paste_combo.addItem(
            "Ctrl+V — chats, browsers, editors (fastest)", userData="ctrl_v"
        )
        self._paste_combo.addItem(
            "Ctrl+Shift+V — terminals (VS Code, Windows Terminal)",
            userData="ctrl_shift_v",
        )
        self._paste_combo.addItem(
            "Type each character — universal, slower", userData="type"
        )
        i = self._paste_combo.findData(config.paste_method)
        if i >= 0:
            self._paste_combo.setCurrentIndex(i)
        self._paste_combo.currentIndexChanged.connect(self._on_changed)
        sl.addWidget(self._paste_combo)

        self._auto_send_cb = QCheckBox(
            "Press Enter after pasting (auto-send for chat apps)"
        )
        self._auto_send_cb.setChecked(config.auto_send)
        self._auto_send_cb.toggled.connect(self._on_changed)
        sl.addWidget(self._auto_send_cb)

        self._preserve_cb = QCheckBox(
            "Restore previous clipboard contents after pasting"
        )
        self._preserve_cb.setChecked(config.preserve_clipboard)
        self._preserve_cb.toggled.connect(self._on_changed)
        sl.addWidget(self._preserve_cb)
        layout.addWidget(_card)

        # ===== Section: Recording popup =====
        _card, sl = self._section("Recording popup")
        self._compact_popup_cb = QCheckBox(
            "Compact recording popup (audio bars only — no word preview)"
        )
        self._compact_popup_cb.setChecked(config.compact_popup)
        self._compact_popup_cb.toggled.connect(self._on_compact_popup_toggle)
        sl.addWidget(self._compact_popup_cb)

        self._follow_cursor_cb = QCheckBox(
            "Popup follows the mouse cursor while recording"
        )
        self._follow_cursor_cb.setChecked(config.popup_follow_cursor)
        self._follow_cursor_cb.toggled.connect(self._on_follow_cursor_toggle)
        sl.addWidget(self._follow_cursor_cb)
        sl.addWidget(
            field_hint(
                "Off by default — if you dictate into a terminal, mouse motion "
                "events can leak as garbage text into your transcript."
            )
        )
        layout.addWidget(_card)

        # ===== Section: Reverse PTT =====
        _card, sl = self._section("Reverse PTT — mute external apps while dictating")
        sl.addWidget(
            field_hint(
                "Slumbr presses a chosen key during dictation so apps like Discord "
                "(via its Push-To-Mute setting) silence your mic externally while "
                "Slumbr keeps capturing internally. Configure the matching keybind "
                "in the other app first. The universal version is below."
            )
        )
        self._reverse_ptt_cb = QCheckBox("Enable reverse PTT during dictation")
        self._reverse_ptt_cb.setChecked(config.reverse_ptt_enabled)
        self._reverse_ptt_cb.toggled.connect(self._on_reverse_ptt_toggle)
        sl.addWidget(self._reverse_ptt_cb)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.setContentsMargins(0, 4, 0, 0)
        row.addWidget(field_label("Key to send:"))
        self._mute_key_btn = _CaptureKeyButton(self._config.reverse_ptt_vk)
        self._mute_key_btn.key_captured.connect(self._on_mute_key_captured)
        row.addWidget(self._mute_key_btn)
        row.addStretch(1)
        sl.addLayout(row)
        self._mute_key_btn.setEnabled(self._reverse_ptt_cb.isChecked())
        layout.addWidget(_card)

        # ===== Section: Virtual mic routing =====
        _card, sl = self._section("Virtual mic routing — universal")
        sl.addWidget(
            field_hint(
                "Pass your mic through a virtual cable (e.g. VB-Audio Virtual Cable) "
                "and Slumbr will silence that cable during dictation. Works in every "
                "call app — Zoom, Teams, Discord, OBS, browser calls. Set the call "
                "app's mic to \"CABLE Output\" after installing."
            )
        )

        self._cables = find_virtual_cables()
        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        status_dot = QLabel("●")
        if self._cables:
            status_dot.setStyleSheet(f"color: {VIOLET_PRIMARY}; font-size: 14px;")
            status_text = QLabel(
                f"Detected {len(self._cables)} usable virtual cable"
                + ("s" if len(self._cables) > 1 else "")
                + "."
            )
        else:
            status_dot.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 14px;")
            status_text = QLabel(
                "No virtual cable detected. Slumbr can install VB-Cable for "
                "you (downloads from vb-audio.com, requires admin + reboot)."
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

        self._mic_routing_cb = QCheckBox("Route my mic through a virtual cable")
        self._mic_routing_cb.setChecked(config.mic_routing_enabled)
        self._mic_routing_cb.toggled.connect(self._on_mic_routing_toggle)
        self._mic_routing_cb.setEnabled(bool(self._cables))
        sl.addWidget(self._mic_routing_cb)

        cable_row = QHBoxLayout()
        cable_row.setSpacing(10)
        cable_row.setContentsMargins(0, 4, 0, 0)
        cable_row.addWidget(field_label("Virtual cable:"))
        self._cable_combo = QComboBox()
        self._cable_combo.addItem("(none)", userData="")
        for _idx, name in self._cables:
            self._cable_combo.addItem(name, userData=name)
        # Select previously-configured cable if it still exists,
        # otherwise auto-pick the first detected cable AND write it
        # into config — without the write, ticking the route checkbox
        # later would leave config.mic_routing_device_name empty and
        # the mirror would silently never open.
        if config.mic_routing_device_name:
            i = self._cable_combo.findData(config.mic_routing_device_name)
            if i >= 0:
                self._cable_combo.setCurrentIndex(i)
        elif self._cables:
            # Index 0 is "(none)"; first real cable is at index 1.
            self._cable_combo.setCurrentIndex(1)
            self._config.mic_routing_device_name = self._cables[0][1]
        self._cable_combo.currentIndexChanged.connect(self._on_cable_changed)
        self._cable_combo.setEnabled(bool(self._cables))
        cable_row.addWidget(self._cable_combo, stretch=1)
        sl.addLayout(cable_row)
        layout.addWidget(_card)

        layout.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scrollable(body))

    def _section(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        """A titled card that groups related settings — gives the tab clear
        visual structure instead of one flat wall of checkboxes."""
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

    def _on_changed(self, *_args) -> None:
        method = self._paste_combo.currentData()
        if method:
            self._config.paste_method = method
        self._config.auto_send = self._auto_send_cb.isChecked()
        self._config.preserve_clipboard = self._preserve_cb.isChecked()
        self.config_changed.emit()

    def _on_reverse_ptt_toggle(self, checked: bool) -> None:
        self._config.reverse_ptt_enabled = checked
        self._mute_key_btn.setEnabled(checked)
        self.config_changed.emit()

    def _on_mute_key_captured(self, vk: int) -> None:
        self._config.reverse_ptt_vk = vk
        self.config_changed.emit()

    def _on_mic_routing_toggle(self, checked: bool) -> None:
        self._config.mic_routing_enabled = checked
        self.config_changed.emit()

    def _on_cable_changed(self, *_args) -> None:
        device = self._cable_combo.currentData() or ""
        self._config.mic_routing_device_name = device
        self.config_changed.emit()

    def _on_compact_popup_toggle(self, checked: bool) -> None:
        self._config.compact_popup = checked
        self.config_changed.emit()

    def _on_follow_cursor_toggle(self, checked: bool) -> None:
        self._config.popup_follow_cursor = checked
        self.config_changed.emit()

    def _on_install_vbcable(self) -> None:
        """Open the install dialog and kick off the VB-Cable installer."""
        dlg = _VBCableInstallDialog(self)
        dlg.exec()


class _CaptureKeyButton(QPushButton):
    """Click-to-capture keybind picker.

    Normal state: shows the current VK's label (e.g. "F23"). Click to
    enter capture mode — the next key press updates the bound VK.
    Pressing Esc while in capture mode cancels without changing.

    We capture via ``keyPressEvent`` rather than the OS-level pynput
    hook so the button doesn't fight the global Caps Lock hook that
    Slumbr already has installed.
    """

    key_captured = Signal(int)  # the new VK

    def __init__(self, initial_vk: int) -> None:
        super().__init__()
        self._vk = initial_vk
        self._capturing = False
        self.setMinimumWidth(180)
        self.setObjectName("primary" if initial_vk else "")
        self._refresh_label()
        self.clicked.connect(self._start_capture)
        # Tab focus + space/enter activation is fine; we want
        # KeyPress events to come through us when capturing.
        self.setFocusPolicy(Qt.StrongFocus)

    def _refresh_label(self) -> None:
        if self._vk:
            self.setText(f"{vk_label(self._vk)} (click to change)")
        else:
            self.setText("Click to set keybind")

    def _start_capture(self) -> None:
        self._capturing = True
        self.setText("Press any key… (Esc to cancel)")
        self.grabKeyboard()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if not self._capturing:
            super().keyPressEvent(event)
            return
        # Cancel on Esc.
        if event.key() == Qt.Key_Escape:
            self._capturing = False
            self.releaseKeyboard()
            self._refresh_label()
            return
        vk = event.nativeVirtualKey()
        if vk:
            self._vk = int(vk)
            self.key_captured.emit(self._vk)
        self._capturing = False
        self.releaseKeyboard()
        self._refresh_label()

    def event(self, ev: QEvent) -> bool:
        # Stop the button's space/enter "activation" behavior from
        # eating the capture. We forward KeyPress through to the
        # capture path above, but let everything else use defaults.
        return super().event(ev)


class _VBCableInstallDialog(QDialog):
    """Modal progress dialog that runs the VB-Cable installer worker.

    Single-shot: open → install runs → dialog stays open with the
    success/failure summary and a Close button. We deliberately don't
    expose mid-install cancel because the elevated installer itself
    isn't easily killable from outside, and stopping our wait wouldn't
    actually cancel Windows's driver install.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Install VB-Cable")
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setMinimumSize(560, 380)
        # Inherit the parent's stylesheet so the dialog looks like the
        # rest of the Settings dialog rather than the system default.
        self.setStyleSheet(parent.styleSheet() if parent else "")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 26, 28, 24)
        outer.setSpacing(14)

        title = QLabel("Installing VB-Cable…")
        tf = title.font()
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
            f"border-radius: 10px; padding: 10px; color: {TEXT_PRIMARY}; "
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

        # Kick off worker.
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
                "show up automatically in Settings → Behavior."
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
