"""Advanced tab — power-user knobs, tucked out of the way.

Slumbr works out of the box, so the fiddly bits live here: paste extras
(auto-send, keep-on-clipboard) and the vocabulary hint. The virtual-cable
device + installer moved to the Voice tab (all mic-input config in one place).
Most users never need to open this tab.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QPlainTextEdit, QVBoxLayout, QWidget

from ...config import SlumbrConfig
from ._widgets import (
    field_hint,
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

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(48, 40, 48, 40)
        layout.setSpacing(20)

        layout.addWidget(heading("Advanced", size=28))
        layout.addWidget(
            subheading(
                "Power-user knobs. Slumbr works out of the box without touching any "
                "of this — change it only if you want to."
            )
        )

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
