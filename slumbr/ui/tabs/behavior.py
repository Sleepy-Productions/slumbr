"""Behavior tab — paste method + the "mute me while dictating" switch.

Kept dead simple: how the transcript gets inserted, and one toggle to keep
call apps from hearing you mid-dictation (via a virtual cable). Power-user
details — the cable picker/installer, auto-send, and the keep-on-clipboard
option — live on the Advanced tab.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QLabel, QVBoxLayout, QWidget

from ...audio.mirror import find_virtual_cables
from ...config import SlumbrConfig
from ...theme import TEXT_SECONDARY
from ._widgets import (
    NoScrollComboBox,
    field_hint,
    field_label,
    heading,
    scrollable,
    section_card,
    subheading,
)

# Where the "Discord push-to-mute" how-to lives. Points at the repo's mute
# section for now; swap to a dedicated how-to page anytime.
_DISCORD_MUTE_HELP = (
    "https://github.com/SIeepyDev/slumbr#mute-other-apps-while-dictating-reverse-ptt"
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
            "Ctrl+V — default, works almost everywhere", userData="ctrl_v"
        )
        self._paste_combo.addItem(
            "Ctrl+Shift+V — fallback if an app ignores Ctrl+V", userData="ctrl_shift_v"
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
                "Stop call apps from hearing you mid-dictation. Route your mic "
                "through a virtual cable that Slumbr silences while you dictate — "
                "works in Discord, Zoom, Teams, OBS, and browser calls."
            )
        )

        self._cables = find_virtual_cables()
        self._mic_routing_cb = QCheckBox("Route my mic through a virtual cable")
        self._mic_routing_cb.setChecked(config.mic_routing_enabled)
        self._mic_routing_cb.setEnabled(bool(self._cables))
        self._mic_routing_cb.toggled.connect(self._on_mic_routing_toggle)
        sl.addWidget(self._mic_routing_cb)
        sl.addWidget(
            field_hint(
                "Pick the cable in Advanced."
                if self._cables
                else "No virtual cable found — install one in Advanced to enable this."
            )
        )

        # Manual alternative for Discord users who'd rather use its push-to-mute.
        link = QLabel(
            f'<a href="{_DISCORD_MUTE_HELP}" style="color:{config.accent_color};">'
            "Prefer Discord's push-to-mute keybind? See the how-to →</a>"
        )
        link.setOpenExternalLinks(True)
        link.setStyleSheet(f"color: {TEXT_SECONDARY};")
        link.setWordWrap(True)
        sl.addWidget(link)
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

    def _on_mic_routing_toggle(self, checked: bool) -> None:
        self._config.mic_routing_enabled = checked
        self.config_changed.emit()
