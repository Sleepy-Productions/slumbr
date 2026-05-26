"""Voice tab — input device + language.

The two knobs almost everyone touches, applied mid-session without an engine
reload. Engine / model selection lives in the Engine tab; vocabulary hints,
auto-corrections, and trailing-filler stripping moved to the Advanced tab.
"""

from __future__ import annotations

import sounddevice as sd
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ...config import SlumbrConfig
from ._widgets import (
    NoScrollComboBox,
    field_hint,
    field_label,
    heading,
    scrollable,
    subheading,
)


def _list_input_devices() -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    try:
        for i, d in enumerate(sd.query_devices()):
            if int(d.get("max_input_channels", 0)) > 0:
                out.append((i, d["name"]))
    except Exception:  # noqa: BLE001
        pass
    return out


class VoiceTab(QWidget):
    config_changed = Signal()

    def __init__(self, config: SlumbrConfig) -> None:
        super().__init__()
        self._config = config

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(48, 40, 48, 40)
        layout.setSpacing(20)

        layout.addWidget(heading("Voice", size=28))
        layout.addWidget(
            subheading(
                "Mic and language — applied immediately, no restart. Vocabulary "
                "hints and auto-corrections are on the Advanced tab."
            )
        )

        # Input device
        layout.addWidget(field_label("Input device"))
        self._device_combo = NoScrollComboBox()
        self._device_combo.addItem("System default", userData=None)
        for _idx, name in _list_input_devices():
            self._device_combo.addItem(name, userData=name)
        if config.input_device_name:
            i = self._device_combo.findData(config.input_device_name)
            if i >= 0:
                self._device_combo.setCurrentIndex(i)
        self._device_combo.currentIndexChanged.connect(self._on_changed)
        layout.addWidget(self._device_combo)

        # Language
        layout.addWidget(field_label("Language"))
        self._language_combo = NoScrollComboBox()
        self._language_combo.addItem("English (recommended)", userData="en")
        self._language_combo.addItem("Auto-detect", userData="")
        self._language_combo.addItem("Spanish", userData="es")
        self._language_combo.addItem("French", userData="fr")
        self._language_combo.addItem("German", userData="de")
        self._language_combo.addItem("Portuguese", userData="pt")
        self._language_combo.addItem("Japanese", userData="ja")
        i = self._language_combo.findData(config.language or "")
        if i >= 0:
            self._language_combo.setCurrentIndex(i)
        self._language_combo.currentIndexChanged.connect(self._on_changed)
        layout.addWidget(self._language_combo)
        layout.addWidget(
            field_hint(
                "Moonshine is English-only — pick another backend in Engine if "
                "you need multi-language dictation."
            )
        )

        layout.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scrollable(body))

    def _on_changed(self, *_args) -> None:
        self._config.input_device_name = self._device_combo.currentData()
        lang = self._language_combo.currentData()
        self._config.language = lang if lang is not None else ""
        self.config_changed.emit()
