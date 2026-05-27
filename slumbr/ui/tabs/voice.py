"""Voice tab — everything about your mic input, in one place.

Two cards:
  • Microphone     — which mic Slumbr listens to (deduped, one entry per
                     physical device) + dictation language.
  • Virtual mic cable — the reverse-PTT routing that mutes other apps while
                     you dictate: detect/install the cable, turn routing on,
                     pick the cable. (Previously split across Behavior +
                     Advanced — consolidated here so there's one obvious home.)

Engine / model selection lives in the Engine tab; vocabulary hints,
auto-corrections, and paste extras live in Advanced.
"""

from __future__ import annotations

import sounddevice as sd
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

from ...audio.mirror import (
    _INPUT_HOST_API_PRIORITY,
    _VIRTUAL_CABLE_KEYWORDS,
    find_virtual_cables,
)
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

# Where the "Discord push-to-mute" how-to lives.
_DISCORD_MUTE_HELP = (
    "https://github.com/SIeepyDev/slumbr#mute-other-apps-while-dictating-reverse-ptt"
)


# Input names that are NOT real microphones: Windows default-router aliases and
# loopback/line sources. Hidden from the mic picker — the user wants to pick a
# MIC, and "System default" already covers default routing.
_NON_MIC_KEYWORDS: tuple[str, ...] = (
    "sound mapper",           # MME default-router alias
    "primary sound capture",  # DirectSound default-router alias
    "stereo mix",             # loopback (record system audio)
    "what u hear",            # loopback (Creative)
    "wave out mix",           # loopback
    "line in",                # line input, not a mic
    "line-in",
)


def _select_mic_devices(raw: list[tuple[int, str, int]]) -> list[tuple[int, str]]:
    """From ``(index, name, host_prio)`` real-mic candidates: keep the best
    host-API instance per name, then drop MME-truncation duplicates — a name
    that is a strict prefix of a fuller kept name ('Microphone (HyperX QuadCast
    2 S' vs '…2 S)'). Returns ``[(index, name), …]`` sorted by name. (Cable +
    non-mic filtering happens upstream in _list_input_devices.)"""
    best: dict[str, tuple[int, int]] = {}  # name -> (index, host_prio)
    for idx, name, prio in raw:
        prev = best.get(name)
        if prev is None or prio < prev[1]:
            best[name] = (idx, prio)
    names = list(best)
    out = [
        (best[n][0], n)
        for n in names
        if not any(other != n and other.startswith(n) for other in names)
    ]
    return sorted(out, key=lambda t: t[1].lower())


def _list_input_devices() -> list[tuple[int, str]]:
    """Real microphones only, ONE entry each.

    Excludes (a) virtual cables — they have their own section below; (b) Windows
    default-router aliases + loopback/line inputs (not mics); and collapses the
    same mic enumerated across host APIs (incl. MME's 31-char name truncation).
    See _select_mic_devices."""
    raw: list[tuple[int, str, int]] = []
    try:
        for i, d in enumerate(sd.query_devices()):
            if int(d.get("max_input_channels", 0)) <= 0:
                continue
            name = str(d.get("name", "")).strip()
            if not name:
                continue
            name_lc = name.lower()
            if any(kw in name_lc for kw in _VIRTUAL_CABLE_KEYWORDS):
                continue  # virtual cable — Virtual mic cable section, not here
            if any(kw in name_lc for kw in _NON_MIC_KEYWORDS):
                continue  # router alias / loopback / line-in — not a mic
            try:
                hostapi = sd.query_hostapis(d.get("hostapi", 0))["name"]
            except (KeyError, IndexError):
                hostapi = ""
            raw.append((i, name, _INPUT_HOST_API_PRIORITY.get(hostapi, 50)))
    except Exception:  # noqa: BLE001
        pass
    return _select_mic_devices(raw)


class VoiceTab(QWidget):
    config_changed = Signal()

    def __init__(self, config: SlumbrConfig) -> None:
        super().__init__()
        self._config = config
        self._status_dot: QLabel | None = None  # accent-colored when a cable is found

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(48, 40, 48, 40)
        layout.setSpacing(20)

        layout.addWidget(heading("Voice", size=28))
        layout.addWidget(
            subheading(
                "Your mic and language. Applied immediately, no restart. Vocabulary "
                "hints and auto-corrections are on the Advanced tab."
            )
        )

        # ===== Microphone =====
        _card, sl = section_card("Microphone")
        sl.addWidget(field_label("Input device"))
        self._device_combo = NoScrollComboBox()
        self._device_combo.addItem("System default", userData=None)
        for _idx, name in _list_input_devices():
            self._device_combo.addItem(name, userData=name)
        if config.input_device_name:
            i = self._device_combo.findData(config.input_device_name)
            if i >= 0:
                self._device_combo.setCurrentIndex(i)
        self._device_combo.currentIndexChanged.connect(self._on_changed)
        sl.addWidget(self._device_combo)
        sl.addWidget(
            field_hint(
                "“System default” follows whatever mic Windows is set to — leave it "
                "here unless you want a specific mic."
            )
        )

        sl.addWidget(field_label("Language"))
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
        sl.addWidget(self._language_combo)
        sl.addWidget(
            field_hint(
                "Moonshine is English-only — pick another backend in Engine if you "
                "need multi-language dictation."
            )
        )
        layout.addWidget(_card)

        # ===== Virtual mic cable (reverse-PTT routing) =====
        _card, sl = section_card("Virtual mic cable")
        sl.addWidget(
            field_hint(
                "Stop call apps from hearing you mid-dictation. Slumbr passes your "
                "mic through a virtual cable and silences it while you dictate — "
                "works in Discord, Zoom, Teams, OBS, and browser calls."
            )
        )

        self._cables = find_virtual_cables()
        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        status_dot = QLabel("●")
        if self._cables:
            status_dot.setStyleSheet(f"color: {config.accent_color}; font-size: 14px;")
            self._status_dot = status_dot
            status_text = QLabel(
                'Cable ready. Set your call app\'s mic to "CABLE Output", then turn '
                "routing on below."
            )
        else:
            status_dot.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 14px;")
            status_text = QLabel(
                "No virtual cable installed yet. Slumbr can install VB-Cable for you "
                "(downloads from vb-audio.com, needs admin + a reboot)."
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

        # The on/off switch (was on the Behavior tab).
        self._mic_routing_cb = QCheckBox("Route my mic through a virtual cable")
        self._mic_routing_cb.setChecked(config.mic_routing_enabled)
        self._mic_routing_cb.setEnabled(bool(self._cables))
        self._mic_routing_cb.toggled.connect(self._on_mic_routing_toggle)
        sl.addWidget(self._mic_routing_cb)

        # The cable picker — ONLY virtual cables, never the wall of mics.
        cable_row = QHBoxLayout()
        cable_row.setSpacing(10)
        cable_row.setContentsMargins(0, 4, 0, 0)
        cable_row.addWidget(field_label("Cable:"))
        self._cable_combo = NoScrollComboBox()
        self._cable_combo.addItem("(none)", userData="")
        for _idx, name in self._cables:
            self._cable_combo.addItem(name, userData=name)
        # Restore the saved cable if it still exists; else auto-pick the first
        # one AND persist it, so ticking the toggle actually opens the mirror
        # instead of silently no-op'ing on an empty device.
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

        # Manual alternative for Discord users who'd rather use push-to-mute.
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
    def _on_changed(self, *_args) -> None:
        self._config.input_device_name = self._device_combo.currentData()
        lang = self._language_combo.currentData()
        self._config.language = lang if lang is not None else ""
        self.config_changed.emit()

    def _on_mic_routing_toggle(self, checked: bool) -> None:
        self._config.mic_routing_enabled = checked
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
                "show up automatically here in Voice."
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
