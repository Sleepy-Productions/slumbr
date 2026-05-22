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

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...config import SlumbrConfig
from ...input.keymap import vk_label
from ._widgets import field_hint, field_label, heading, scrollable, subheading


class BehaviorTab(QWidget):
    config_changed = Signal()

    def __init__(self, config: SlumbrConfig) -> None:
        super().__init__()
        self._config = config

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(56, 48, 56, 48)
        layout.setSpacing(22)

        layout.addWidget(heading("Behavior", size=28))
        layout.addWidget(
            subheading(
                "How Slumbr inserts the transcript into the focused window, "
                "and how it plays nice with calls."
            )
        )

        # Paste method
        layout.addWidget(field_label("Paste method"))
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
        layout.addWidget(self._paste_combo)

        # Auto-send
        self._auto_send_cb = QCheckBox(
            "Press Enter after pasting (auto-send for chat apps)"
        )
        self._auto_send_cb.setChecked(config.auto_send)
        self._auto_send_cb.toggled.connect(self._on_changed)
        layout.addWidget(self._auto_send_cb)

        # Preserve clipboard
        self._preserve_cb = QCheckBox(
            "Restore previous clipboard contents after pasting"
        )
        self._preserve_cb.setChecked(config.preserve_clipboard)
        self._preserve_cb.toggled.connect(self._on_changed)
        layout.addWidget(self._preserve_cb)

        # ----- Reverse PTT
        layout.addSpacing(10)
        layout.addWidget(field_label("Reverse PTT (mute external apps while dictating)"))
        layout.addWidget(
            field_hint(
                "Slumbr presses a chosen key during dictation so apps like Discord "
                "(via its Push-To-Mute setting) silence your mic externally while "
                "Slumbr keeps capturing internally. Configure the matching keybind "
                "in the other app first. Universal multi-app reverse-PTT (via "
                "virtual mic routing) is on the roadmap."
            )
        )

        self._reverse_ptt_cb = QCheckBox(
            "Enable reverse PTT during dictation"
        )
        self._reverse_ptt_cb.setChecked(config.reverse_ptt_enabled)
        self._reverse_ptt_cb.toggled.connect(self._on_reverse_ptt_toggle)
        layout.addWidget(self._reverse_ptt_cb)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.setContentsMargins(0, 4, 0, 0)
        row.addWidget(field_label("Key to send:"))
        self._mute_key_btn = _CaptureKeyButton(self._config.reverse_ptt_vk)
        self._mute_key_btn.key_captured.connect(self._on_mute_key_captured)
        row.addWidget(self._mute_key_btn)
        row.addStretch(1)
        layout.addLayout(row)

        # Visibility-gate the keybind picker on the checkbox
        self._mute_key_btn.setEnabled(self._reverse_ptt_cb.isChecked())

        layout.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scrollable(body))

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
