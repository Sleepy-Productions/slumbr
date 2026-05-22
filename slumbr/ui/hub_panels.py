"""Content panels for the MainWindow hub.

Each panel is a self-contained QWidget. They communicate state changes
back to the app through signals on the parent MainWindow — the panels
themselves don't import the app layer.
"""

from __future__ import annotations

from collections.abc import Callable

import sounddevice as sd
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..config import SlumbrConfig
from ..input.keymap import vk_label
from ..state import State
from ..theme import (
    BG_DARK,
    BORDER,
    COLOR_IDLE,
    COLOR_PASTING,
    COLOR_RECORDING,
    COLOR_TRANSCRIBING,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    VIOLET_PRIMARY,
)
from .keyboard_picker import KeyboardPicker

_REPO_URL = "https://github.com/SIeepyDev/slumbr"

_STATE_COLORS: dict[State, str] = {
    State.IDLE: COLOR_IDLE,
    State.RECORDING: COLOR_RECORDING,
    State.TRANSCRIBING: COLOR_TRANSCRIBING,
    State.PASTING: COLOR_PASTING,
}

_STATE_LABELS: dict[State, str] = {
    State.IDLE: "Idle",
    State.RECORDING: "Recording",
    State.TRANSCRIBING: "Transcribing",
    State.PASTING: "Pasting",
}


# ---------------------------------------------------------------------------
# Reusable building blocks
# ---------------------------------------------------------------------------


def _heading(text: str, size: int = 22, bold: bool = True) -> QLabel:
    lbl = QLabel(text)
    f = QFont()
    f.setPointSize(size)
    f.setBold(bold)
    lbl.setFont(f)
    return lbl


def _subheading(text: str) -> QLabel:
    lbl = QLabel(text)
    f = QFont()
    f.setPointSize(11)
    lbl.setFont(f)
    lbl.setStyleSheet(f"color: {TEXT_SECONDARY};")
    lbl.setWordWrap(True)
    return lbl


def _field_label(text: str) -> QLabel:
    lbl = QLabel(text)
    f = QFont()
    f.setPointSize(10)
    f.setBold(True)
    lbl.setFont(f)
    return lbl


def _field_hint(text: str) -> QLabel:
    lbl = QLabel(text)
    f = QFont()
    f.setPointSize(9)
    lbl.setFont(f)
    lbl.setStyleSheet(f"color: {TEXT_SECONDARY};")
    lbl.setWordWrap(True)
    return lbl


def _card() -> QFrame:
    card = QFrame()
    card.setObjectName("card")
    return card


def _scrollable(content: QWidget) -> QScrollArea:
    """Wrap `content` so panels with lots of fields scroll on small windows."""
    sc = QScrollArea()
    sc.setWidget(content)
    sc.setWidgetResizable(True)
    sc.setFrameShape(QFrame.NoFrame)
    sc.setStyleSheet(f"QScrollArea {{ background: {BG_DARK}; border: none; }}")
    return sc


# ---------------------------------------------------------------------------
# Home panel
# ---------------------------------------------------------------------------


class HomePanel(QWidget):
    """Big-status landing page. Shows current state, last transcript, primary CTA."""

    def __init__(
        self,
        config: SlumbrConfig,
        on_toggle: Callable[[], None],
    ) -> None:
        super().__init__()
        self._on_toggle = on_toggle
        self._config = config

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(56, 48, 56, 48)
        layout.setSpacing(26)

        # ----- hero
        hero_eyebrow = QLabel("DICTATION HUB")
        ef = QFont()
        ef.setPointSize(9)
        ef.setBold(True)
        hero_eyebrow.setFont(ef)
        hero_eyebrow.setStyleSheet(
            f"color: {VIOLET_PRIMARY}; letter-spacing: 2px;"
        )
        layout.addWidget(hero_eyebrow)

        hero = _heading("Speak. We'll handle the typing.", size=32)
        hero.setWordWrap(True)
        layout.addWidget(hero)

        layout.addWidget(
            _subheading(
                "Tap your hotkey and start talking. Slumbr listens locally, "
                "transcribes with Whisper large-v3, and pastes at your cursor."
            )
        )

        # ----- status card (hero variant — violet border)
        status_card = QFrame()
        status_card.setObjectName("hero-card")
        status_layout = QHBoxLayout(status_card)
        status_layout.setContentsMargins(28, 26, 28, 26)
        status_layout.setSpacing(22)

        self._status_dot = QLabel()
        self._status_dot.setFixedSize(26, 26)
        self._set_dot_color(COLOR_IDLE)

        status_text = QVBoxLayout()
        status_text.setSpacing(4)
        self._status_label = _heading(_STATE_LABELS[State.IDLE], size=20, bold=True)
        sub_row = QHBoxLayout()
        sub_row.setSpacing(8)
        sub_row.setContentsMargins(0, 0, 0, 0)
        self._status_lead = QLabel("Tap ")
        self._status_lead.setStyleSheet(f"color: {TEXT_SECONDARY};")
        self._hotkey_pill = QLabel(vk_label(config.hotkey_vk))
        self._hotkey_pill.setObjectName("hotkey-pill")
        self._status_tail = QLabel("to start dictation.")
        self._status_tail.setStyleSheet(f"color: {TEXT_SECONDARY};")
        sub_row.addWidget(self._status_lead)
        sub_row.addWidget(self._hotkey_pill)
        sub_row.addWidget(self._status_tail)
        sub_row.addStretch(1)
        status_text.addWidget(self._status_label)
        status_text.addLayout(sub_row)

        status_layout.addWidget(self._status_dot)
        status_layout.addLayout(status_text, stretch=1)

        self._toggle_btn = QPushButton("Toggle recording")
        self._toggle_btn.setObjectName("primary")
        self._toggle_btn.setMinimumHeight(46)
        self._toggle_btn.setMinimumWidth(190)
        self._toggle_btn.clicked.connect(lambda: self._on_toggle())
        status_layout.addWidget(self._toggle_btn)

        layout.addWidget(status_card)

        # ----- last transcript
        transcript_header = QLabel("Last transcript")
        thf = QFont()
        thf.setPointSize(11)
        thf.setBold(True)
        transcript_header.setFont(thf)
        layout.addWidget(transcript_header)

        self._last_transcript = QTextEdit()
        self._last_transcript.setReadOnly(True)
        self._last_transcript.setPlaceholderText(
            "Your most recent dictation will appear here. Tap your hotkey to begin."
        )
        self._last_transcript.setMinimumHeight(180)
        layout.addWidget(self._last_transcript, stretch=1)

        # Outer wrap in a scroll area
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(_scrollable(body))

    # ----- external API
    def set_state(self, state: State) -> None:
        self._set_dot_color(_STATE_COLORS[state])
        self._status_label.setText(_STATE_LABELS[state])

    def set_last_transcript(self, text: str) -> None:
        self._last_transcript.setPlainText(text)

    def set_hotkey_label(self, label: str) -> None:
        self._hotkey_pill.setText(label)

    def _set_dot_color(self, color: str) -> None:
        # Bigger dot to match hero-card scale (26x26 → radius 13).
        self._status_dot.setStyleSheet(
            f"background-color: {color}; border-radius: 13px; border: 1px solid {BORDER};"
        )


# ---------------------------------------------------------------------------
# Shortcuts panel — the visual keyboard binder
# ---------------------------------------------------------------------------


class ShortcutsPanel(QWidget):
    hotkey_changed = Signal(int)  # new VK

    def __init__(self, config: SlumbrConfig) -> None:
        super().__init__()
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(56, 48, 56, 48)
        layout.setSpacing(22)

        layout.addWidget(_heading("Shortcuts", size=28))
        layout.addWidget(
            _subheading(
                "Click any key on the keyboard below to bind it as your dictation "
                "hotkey. Modifiers (Shift, Ctrl, Alt, Win), Space, Enter, Tab and "
                "Esc are disabled because they're load-bearing for normal typing."
            )
        )

        # Current binding pill — gives the user immediate confirmation of
        # what's active without having to scan the keyboard for the highlight.
        binding_row = QHBoxLayout()
        binding_row.setSpacing(10)
        binding_row.setContentsMargins(0, 8, 0, 0)
        binding_lead = QLabel("Bound to")
        bl = QFont()
        bl.setPointSize(10)
        binding_lead.setFont(bl)
        binding_lead.setStyleSheet(f"color: {TEXT_SECONDARY};")
        self._bound_pill = QLabel(vk_label(config.hotkey_vk))
        self._bound_pill.setObjectName("hotkey-pill")
        binding_row.addWidget(binding_lead)
        binding_row.addWidget(self._bound_pill)
        binding_row.addStretch(1)
        layout.addLayout(binding_row)

        self._picker = KeyboardPicker(current_vk=config.hotkey_vk)
        self._picker.key_chosen.connect(self._on_picker_change)
        layout.addWidget(self._picker)

        tip = _field_hint(
            "The change takes effect immediately — you don't need to restart "
            "Slumbr. Whatever you bind here gets fully consumed by Slumbr while "
            "the app is running (its normal OS behavior is suppressed)."
        )
        layout.addWidget(tip)
        layout.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(_scrollable(body))

    def _on_picker_change(self, vk: int) -> None:
        self._bound_pill.setText(vk_label(vk))
        self.hotkey_changed.emit(vk)

    def set_hotkey(self, vk: int) -> None:
        self._picker.set_current_vk(vk)
        self._bound_pill.setText(vk_label(vk))


# ---------------------------------------------------------------------------
# Voice panel — STT-related settings
# ---------------------------------------------------------------------------


def _list_input_devices() -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    try:
        for i, d in enumerate(sd.query_devices()):
            if int(d.get("max_input_channels", 0)) > 0:
                out.append((i, d["name"]))
    except Exception:  # noqa: BLE001
        pass
    return out


class VoicePanel(QWidget):
    config_changed = Signal()

    def __init__(self, config: SlumbrConfig) -> None:
        super().__init__()
        self._config = config

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(56, 48, 56, 48)
        layout.setSpacing(22)

        layout.addWidget(_heading("Voice", size=28))
        layout.addWidget(
            _subheading(
                "Configure what listens, what transcribes, and how Slumbr decides "
                "what you said. Settings apply immediately unless noted."
            )
        )

        # Input device
        layout.addWidget(_field_label("Input device"))
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
        layout.addWidget(_field_label("Language"))
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

        # Model
        layout.addWidget(_field_label("Whisper model"))
        self._model_combo = QComboBox()
        self._model_combo.addItem("large-v3 — most accurate (~3 GB)", userData="large-v3")
        self._model_combo.addItem(
            "large-v3-turbo — faster, slightly less accurate", userData="large-v3-turbo"
        )
        self._model_combo.addItem("distil-large-v3 — middle ground", userData="distil-large-v3")
        self._model_combo.addItem("medium — small VRAM", userData="medium")
        i = self._model_combo.findData(config.model_size)
        if i >= 0:
            self._model_combo.setCurrentIndex(i)
        self._model_combo.currentIndexChanged.connect(self._on_changed)
        layout.addWidget(self._model_combo)
        layout.addWidget(_field_hint("Restart Slumbr for model changes to take effect."))

        # Compute type
        layout.addWidget(_field_label("Compute precision"))
        self._compute_combo = QComboBox()
        self._compute_combo.addItem(
            "int8_float16 — best accuracy/speed", userData="int8_float16"
        )
        self._compute_combo.addItem("int8 — smallest VRAM", userData="int8")
        self._compute_combo.addItem("float16 — most accurate, more VRAM", userData="float16")
        i = self._compute_combo.findData(config.compute_type)
        if i >= 0:
            self._compute_combo.setCurrentIndex(i)
        self._compute_combo.currentIndexChanged.connect(self._on_changed)
        layout.addWidget(self._compute_combo)
        layout.addWidget(_field_hint("Restart Slumbr for precision changes to take effect."))

        # Vocabulary hint
        layout.addWidget(_field_label("Vocabulary hint"))
        layout.addWidget(
            _field_hint(
                "List proper nouns, technical terms, slang, or anything Whisper "
                "is mishearing. Up to ~200 tokens."
            )
        )
        self._prompt_edit = QPlainTextEdit()
        self._prompt_edit.setPlainText(config.initial_prompt)
        self._prompt_edit.setPlaceholderText(
            "Slumbr, Sleepy Productions, PySide6, faster-whisper, sherpa-onnx, "
            "Caps Lock, dictation..."
        )
        self._prompt_edit.setFixedHeight(110)
        self._prompt_edit.textChanged.connect(self._on_changed)
        layout.addWidget(self._prompt_edit)

        layout.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(_scrollable(body))

    def _on_changed(self, *_args) -> None:
        # Push UI state into the dataclass; main_window will persist + notify.
        self._config.input_device_name = self._device_combo.currentData()
        lang = self._language_combo.currentData()
        self._config.language = lang if lang is not None else ""
        model = self._model_combo.currentData()
        if model:
            self._config.model_size = model
        ctype = self._compute_combo.currentData()
        if ctype:
            self._config.compute_type = ctype
        self._config.initial_prompt = self._prompt_edit.toPlainText().strip()
        self.config_changed.emit()


# ---------------------------------------------------------------------------
# Behavior panel — paste, clipboard, close-to-tray
# ---------------------------------------------------------------------------


class BehaviorPanel(QWidget):
    config_changed = Signal()

    def __init__(self, config: SlumbrConfig) -> None:
        super().__init__()
        self._config = config

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(56, 48, 56, 48)
        layout.setSpacing(22)

        layout.addWidget(_heading("Behavior", size=28))
        layout.addWidget(
            _subheading(
                "How Slumbr inserts your transcript into the focused window, and "
                "how it behaves when you close the main window."
            )
        )

        # Paste method
        layout.addWidget(_field_label("Paste method"))
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
        self._auto_send_cb = QCheckBox("Press Enter after pasting (auto-send for chat apps)")
        self._auto_send_cb.setChecked(config.auto_send)
        self._auto_send_cb.toggled.connect(self._on_changed)
        layout.addWidget(self._auto_send_cb)

        # Preserve clipboard
        self._preserve_cb = QCheckBox("Restore previous clipboard contents after pasting")
        self._preserve_cb.setChecked(config.preserve_clipboard)
        self._preserve_cb.toggled.connect(self._on_changed)
        layout.addWidget(self._preserve_cb)

        # Close-to-tray
        self._close_to_tray_cb = QCheckBox("Close to tray instead of quitting")
        self._close_to_tray_cb.setChecked(config.close_to_tray)
        self._close_to_tray_cb.toggled.connect(self._on_changed)
        layout.addWidget(self._close_to_tray_cb)

        layout.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(_scrollable(body))

    def _on_changed(self, *_args) -> None:
        method = self._paste_combo.currentData()
        if method:
            self._config.paste_method = method
        self._config.auto_send = self._auto_send_cb.isChecked()
        self._config.preserve_clipboard = self._preserve_cb.isChecked()
        # When the user toggles close-to-tray here we also record that they've
        # chosen — suppresses the first-close prompt forever after.
        self._config.close_to_tray = self._close_to_tray_cb.isChecked()
        self._config.close_choice_made = True
        self.config_changed.emit()


# ---------------------------------------------------------------------------
# About panel
# ---------------------------------------------------------------------------


class AboutPanel(QWidget):
    quit_requested = Signal()

    def __init__(self) -> None:
        super().__init__()

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(56, 48, 56, 48)
        layout.setSpacing(22)

        layout.addWidget(_heading("About Slumbr", size=28))

        brand = QLabel("Sleepy Productions")
        brand.setStyleSheet(f"color: {VIOLET_PRIMARY}; font-weight: 700;")
        bf = QFont()
        bf.setPointSize(13)
        brand.setFont(bf)
        layout.addWidget(brand)

        version = QLabel(f"Version {__version__}")
        version.setStyleSheet(f"color: {TEXT_SECONDARY};")
        layout.addWidget(version)

        tagline = QLabel(
            "Local, offline voice dictation for Windows. Fully on-device — "
            "no accounts, no cloud, no telemetry."
        )
        tagline.setWordWrap(True)
        tagline.setStyleSheet(f"color: {TEXT_PRIMARY}; padding-top: 6px;")
        layout.addWidget(tagline)

        link = QLabel(
            f'<a href="{_REPO_URL}" style="color: {VIOLET_PRIMARY};">{_REPO_URL}</a>'
        )
        link.setOpenExternalLinks(True)
        link.setTextInteractionFlags(Qt.TextBrowserInteraction)
        layout.addWidget(link)

        license_label = QLabel("Released under the MIT License.")
        license_label.setStyleSheet(f"color: {TEXT_SECONDARY};")
        layout.addWidget(license_label)

        # Footer with quit
        layout.addSpacing(20)
        quit_btn = QPushButton("Quit Slumbr")
        quit_btn.setObjectName("destructive")
        quit_btn.setMinimumHeight(36)
        quit_btn.setMaximumWidth(160)
        quit_btn.clicked.connect(lambda: self.quit_requested.emit())
        layout.addWidget(quit_btn)

        layout.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(_scrollable(body))
