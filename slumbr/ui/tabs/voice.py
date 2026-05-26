"""Voice tab — input device, language, vocabulary hint.

These are the *hot-tunable* knobs that apply mid-session without an
engine reload. Engine / model selection moved to the Engine tab.
"""

from __future__ import annotations

import sounddevice as sd
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...config import SlumbrConfig
from ._widgets import field_hint, field_label, heading, scrollable, subheading


def _list_input_devices() -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    try:
        for i, d in enumerate(sd.query_devices()):
            if int(d.get("max_input_channels", 0)) > 0:
                out.append((i, d["name"]))
    except Exception:  # noqa: BLE001
        pass
    return out


def _format_replacements(d: dict[str, str]) -> str:
    """Render the {heard: corrected} map as editable 'heard => corrected' lines."""
    return "\n".join(f"{k} => {v}" for k, v in d.items())


def _parse_replacements(text: str) -> dict[str, str]:
    """Parse 'heard => corrected' (or '->') lines back into a map. Malformed
    lines are skipped silently so a half-typed entry never breaks the rest.
    """
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        for sep in ("=>", "->"):
            if sep in line:
                left, right = line.split(sep, 1)
                left, right = left.strip(), right.strip()
                if left and right:
                    out[left] = right
                break
    return out


class VoiceTab(QWidget):
    config_changed = Signal()

    def __init__(self, config: SlumbrConfig) -> None:
        super().__init__()
        self._config = config

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(56, 48, 56, 48)
        layout.setSpacing(22)

        layout.addWidget(heading("Voice", size=28))
        layout.addWidget(
            subheading(
                "Mic, language, and vocabulary hints. These apply immediately — "
                "no restart needed."
            )
        )

        # Input device
        layout.addWidget(field_label("Input device"))
        self._device_combo = QComboBox()
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
        self._language_combo = QComboBox()
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

        # Vocabulary hint
        layout.addWidget(field_label("Vocabulary hint"))
        layout.addWidget(
            field_hint(
                "List proper nouns, technical terms, slang — anything Slumbr "
                "mishears. Up to ~200 tokens. Used as Whisper's initial_prompt "
                "(Moonshine ignores this field)."
            )
        )
        self._prompt_edit = QPlainTextEdit()
        self._prompt_edit.setPlainText(config.initial_prompt)
        self._prompt_edit.setPlaceholderText(
            "Slumbr, Sleepy Productions, PySide6, faster-whisper, sherpa-onnx..."
        )
        self._prompt_edit.setFixedHeight(110)
        self._prompt_edit.textChanged.connect(self._on_changed)
        layout.addWidget(self._prompt_edit)

        # Auto-corrections (find-replace, every backend)
        layout.addWidget(field_label("Auto-corrections"))
        layout.addWidget(
            field_hint(
                "Fix mishears Slumbr makes the same way every time. One per line, "
                "format: heard => corrected (e.g. keybinde => keybinds). Whole-word, "
                "case-insensitive, applied to every backend before paste."
            )
        )
        self._repl_edit = QPlainTextEdit()
        self._repl_edit.setPlainText(_format_replacements(config.word_replacements))
        self._repl_edit.setPlaceholderText("keybinde => keybinds\nslumber => Slumbr")
        self._repl_edit.setFixedHeight(90)
        self._repl_edit.textChanged.connect(self._on_changed)
        layout.addWidget(self._repl_edit)

        self._strip_filler = QCheckBox(
            "Remove trailing “thank you” / “thanks for watching” hallucinations"
        )
        self._strip_filler.setChecked(config.strip_trailing_filler)
        self._strip_filler.stateChanged.connect(self._on_changed)
        layout.addWidget(self._strip_filler)

        layout.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scrollable(body))

    def _on_changed(self, *_args) -> None:
        self._config.input_device_name = self._device_combo.currentData()
        lang = self._language_combo.currentData()
        self._config.language = lang if lang is not None else ""
        self._config.initial_prompt = self._prompt_edit.toPlainText().strip()
        self._config.word_replacements = _parse_replacements(self._repl_edit.toPlainText())
        self._config.strip_trailing_filler = self._strip_filler.isChecked()
        self.config_changed.emit()
