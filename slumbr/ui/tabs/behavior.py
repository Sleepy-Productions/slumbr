"""Behavior tab — paste method + the two "mute me while dictating" toggles.

Kept deliberately simple: how the transcript gets inserted, and the two
on/off switches for keeping call apps from hearing you mid-dictation. Their
details — the reverse-PTT keybind, the virtual-cable device + installer, and
paste extras (auto-send, clipboard restore) — live on the Advanced tab.

The toggles here and their details on Advanced both read/write the same
``SlumbrConfig`` fields, so they stay in sync across tabs without coupling.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QVBoxLayout, QWidget

from ...audio.mirror import find_virtual_cables
from ...config import SlumbrConfig
from ._widgets import (
    NoScrollComboBox,
    field_hint,
    field_label,
    heading,
    scrollable,
    section_card,
    subheading,
)


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
                "How Slumbr inserts the transcript, and how it plays nice with "
                "calls. Fine-tuning lives in the Advanced tab."
            )
        )

        # ===== Pasting =====
        _card, sl = section_card("Pasting")
        sl.addWidget(field_label("Paste method"))
        self._paste_combo = NoScrollComboBox()
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
        self._paste_combo.currentIndexChanged.connect(self._on_paste_changed)
        sl.addWidget(self._paste_combo)
        layout.addWidget(_card)

        # ===== Mute other apps while dictating =====
        _card, sl = section_card("Mute other apps while dictating")
        sl.addWidget(
            field_hint(
                "Stop call apps from hearing you mid-dictation. Turn on a method "
                "here; configure its details (the key / the cable) in Advanced."
            )
        )

        self._reverse_ptt_cb = QCheckBox(
            "Reverse PTT — press a key so an app like Discord mutes you via its "
            "push-to-mute"
        )
        self._reverse_ptt_cb.setChecked(config.reverse_ptt_enabled)
        self._reverse_ptt_cb.toggled.connect(self._on_reverse_ptt_toggle)
        sl.addWidget(self._reverse_ptt_cb)

        # The virtual-mic toggle needs a cable to exist; enable accordingly and
        # point at Advanced for setup. (Cable detection + picker live there.)
        self._cables = find_virtual_cables()
        self._mic_routing_cb = QCheckBox(
            "Virtual mic — route through a virtual cable that's muted while you "
            "dictate (works in every call app)"
        )
        self._mic_routing_cb.setChecked(config.mic_routing_enabled)
        self._mic_routing_cb.setEnabled(bool(self._cables))
        self._mic_routing_cb.toggled.connect(self._on_mic_routing_toggle)
        sl.addWidget(self._mic_routing_cb)
        sl.addWidget(
            field_hint(
                "Pick the cable + set the reverse-PTT key in Advanced."
                if self._cables
                else "No virtual cable found — install one in Advanced to enable this."
            )
        )
        layout.addWidget(_card)

        layout.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scrollable(body))

    # ----------------------------------------------------------- handlers
    def _on_paste_changed(self, *_args) -> None:
        method = self._paste_combo.currentData()
        if method:
            self._config.paste_method = method
        self.config_changed.emit()

    def _on_reverse_ptt_toggle(self, checked: bool) -> None:
        self._config.reverse_ptt_enabled = checked
        self.config_changed.emit()

    def _on_mic_routing_toggle(self, checked: bool) -> None:
        self._config.mic_routing_enabled = checked
        self.config_changed.emit()
