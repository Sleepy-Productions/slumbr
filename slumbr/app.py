"""QApplication wiring — tray + popup + hotkey + settings dialog.

Threading model
---------------
- Qt main thread owns the state machine, popup, paste, transcriber call
  sites, and the Settings dialog.
- The pynput hook thread emits ``bridge.toggle`` via signal, which lands
  on the main thread via QueuedConnection.
- The pystray thread (tray loop) emits ``bridge.toggle``,
  ``bridge.open_settings``, or ``bridge.quit_requested`` the same way.
- Audio is captured by sounddevice's PortAudio thread — that callback
  emits ``bridge.audio_chunk`` (queued) so the popup's visualizer can
  redraw on the main thread without touching Qt widgets from PortAudio.
- Transcription runs inside ``TranscribeWorker`` (a QThread, see
  ``slumbr/stt/worker.py``). ``done`` and ``failed`` auto-queue back to
  the main thread.

Startup ordering
----------------
``slumbr/__init__.py`` already preloads ``ctranslate2`` (which forces
the NVIDIA CUDA DLLs into the process) BEFORE PySide6 imports happen,
so we can freely construct QApplication first and then build the
transcriber via the factory. The old "engine before QApplication"
constraint is satisfied by the import-time bootstrap.

The first-launch wizard runs as a modal ``QDialog.exec()`` before any
tray icon, hotkey hook, or recorder gets installed — so the nested
event loop has nothing to fight.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QDialog

from . import history
from .audio.capture import SAMPLE_RATE, AudioRecorder
from .config import SlumbrConfig
from .input.foreground import ForegroundTracker
from .input.hotkey import Hotkey
from .input.keymap import vk_label
from .input.mute_key import MuteKeyController
from .input.paste import paste_text
from .polish import polish
from .state import State, StateMachine
from .stt.factory import build_transcriber
from .stt.protocol import Transcriber
from .stt.streaming_engine import StreamingASREngine
from .stt.worker import TranscribeWorker
from .ui.popup import RecordingPopup
from .ui.settings_dialog import SettingsDialog
from .ui.setup_wizard import SetupWizard
from .ui.tray import SlumbrTray

log = logging.getLogger(__name__)

_ASSET_DIR = Path(__file__).resolve().parent / "assets"
_ICON_PATH = _ASSET_DIR / "icon.ico"

MIN_AUDIO_SECONDS = 0.3


class _Bridge(QObject):
    toggle = Signal(str)            # source tag: "hotkey", "tray"
    open_settings = Signal()
    quit_requested = Signal()
    audio_chunk = Signal(object)    # numpy ndarray payload


class SlumbrApp:
    def __init__(self) -> None:
        # ----- QApplication first, so the wizard can use Qt widgets.
        self.qapp = QApplication(sys.argv)
        self.qapp.setQuitOnLastWindowClosed(False)
        self._app_icon: QIcon | None = None
        if _ICON_PATH.is_file():
            self._app_icon = QIcon(str(_ICON_PATH))
            self.qapp.setWindowIcon(self._app_icon)

        # ----- Config (with legacy → BackendConfig migration).
        self.config = SlumbrConfig.load()

        # ----- First-launch wizard if backend isn't set yet.
        if self.config.backend is None:
            log.info("first launch: showing setup wizard")
            wizard = SetupWizard(self.config, app_icon=self._app_icon)
            result = wizard.exec()
            if result != QDialog.Accepted or self.config.backend is None:
                log.info("setup wizard cancelled — exiting")
                raise SystemExit(0)

        log.info(
            "selected backend=%s model=%s",
            self.config.backend.name,
            self.config.backend.model,
        )

        # ----- Transcriber (primary STT engine).
        self.transcriber: Transcriber = build_transcriber(
            self.config.backend,
            language=self.config.language or None,
            initial_prompt=self.config.initial_prompt,
        )
        self.transcriber.warm_up()

        # ----- Streaming engine for live popup partials. Always Moonshine
        # on CPU; runs in parallel with whatever primary backend the user
        # picked so popup partials work even on NVIDIA where Whisper isn't
        # streaming-native.
        self.streaming_engine = StreamingASREngine(
            enable_streaming_leading_edge=self.config.streaming_visual_leading_edge,
        )

        # ----- App state + popup + foreground tracker.
        self.state = StateMachine()
        self.popup = RecordingPopup()
        self.foreground = ForegroundTracker()
        self.foreground.start()
        self._paste_target_hwnd: int | None = None

        # ----- Mid-session backend-swap lock. Held while Settings is
        # tearing down the old transcriber + warming the new one; the
        # hotkey handler short-circuits while held so a Caps Lock tap
        # during the swap doesn't race a half-destroyed engine. Phase 1
        # leaves the actual swap unwired (changes require restart per
        # the Engine tab's notice); the lock is here for Phase 3.
        self._swap_lock = threading.Lock()

        # ----- Bridge signals (cross-thread → main thread).
        self.bridge = _Bridge()
        self.bridge.toggle.connect(self._on_toggle, Qt.QueuedConnection)
        self.bridge.open_settings.connect(self._on_open_settings, Qt.QueuedConnection)
        self.bridge.quit_requested.connect(self._on_quit, Qt.QueuedConnection)
        self.bridge.audio_chunk.connect(self._on_audio_chunk, Qt.QueuedConnection)

        # ----- Audio recorder.
        self.recorder = AudioRecorder(
            device=self.config.input_device_name,
            on_chunk=self._on_audio_thread_chunk,
        )

        # ----- Tray.
        self.tray = SlumbrTray(
            on_toggle=lambda: self.bridge.toggle.emit("tray"),
            on_settings=self.bridge.open_settings.emit,
            on_quit=self.bridge.quit_requested.emit,
            hotkey_label=vk_label(self.config.hotkey_vk),
        )
        self.tray.start()

        # ----- Hotkey hook (low-level WH_KEYBOARD_LL).
        self.hotkey = Hotkey(
            vk=self.config.hotkey_vk,
            on_press=lambda: self.bridge.toggle.emit("hotkey"),
        )
        self.hotkey.start()

        # ----- Elapsed-timer for popup MM:SS during recording.
        self._elapsed_timer = QTimer()
        self._elapsed_timer.setInterval(200)
        self._elapsed_timer.timeout.connect(self._update_elapsed)
        self._record_started_at = 0.0

        # ----- Per-utterance worker handle.
        self._worker: TranscribeWorker | None = None

        # ----- Wall-clock anchor for end-to-end latency tracing.
        self._t_stop_pressed: float = 0.0

        # ----- Reverse-PTT mute-key sender. Armed only if the user has
        # both enabled the feature AND picked a VK to send. See
        # ``slumbr/input/mute_key.py`` for the workaround rationale.
        self.mute_key = MuteKeyController()
        if self.config.reverse_ptt_enabled and self.config.reverse_ptt_vk:
            self.mute_key.arm(self.config.reverse_ptt_vk)

        # ----- Settings dialog is built lazily on first open so startup
        # doesn't pay the cost for users who never touch it.
        self._settings_dialog: SettingsDialog | None = None

        log.info(
            "ready. Tap %s to start/stop. Quit from the tray.",
            vk_label(self.config.hotkey_vk),
        )

    # ------------------------------------------------------- audio chunk hop
    def _on_audio_thread_chunk(self, samples: np.ndarray) -> None:
        # Called on PortAudio thread. Marshal onto Qt main thread.
        self.bridge.audio_chunk.emit(samples.copy())

    def _on_audio_chunk(self, samples) -> None:
        # On Qt main thread.
        self.popup.push_samples(samples)
        if self.state.state is State.RECORDING:
            try:
                partial = self.streaming_engine.feed(samples)
            except Exception as e:  # noqa: BLE001
                log.warning("streaming-engine feed failed: %s", e)
                return
            if partial.committed or partial.tentative:
                self.popup.set_partial(partial.committed, partial.tentative)

    # --------------------------------------------------------------- toggle
    def _on_toggle(self, source: str = "?") -> None:
        if not self._swap_lock.acquire(blocking=False):
            log.info("toggle ignored — backend swap in progress")
            return
        try:
            current = self.state.state
            log.debug("toggle source=%s state=%s", source, current.value)
            if current is State.IDLE:
                self._start_recording()
            elif current is State.RECORDING:
                self._stop_and_transcribe()
            else:
                log.debug("ignore toggle during %s", current.value)
        finally:
            self._swap_lock.release()

    def _start_recording(self) -> None:
        if not self.state.try_transition(State.RECORDING):
            return
        t_toggle = time.monotonic()
        self._paste_target_hwnd = self.foreground.last_hwnd()
        log.info("IDLE -> RECORDING (target hwnd=%s)", self._paste_target_hwnd)
        # Send the reverse-PTT mute key (e.g. Discord's PTM keybind)
        # BEFORE we start capture so the other app silences us in time.
        self.mute_key.press()
        self.popup.show_recording()
        try:
            self.recorder.start()
        except Exception as e:  # noqa: BLE001
            log.error("could not start recording: %s", e)
            self._reset_to_idle()
            return
        self.streaming_engine.start_session()
        prebuffer_audio = self.recorder.snapshot()
        if prebuffer_audio is not None and prebuffer_audio.size > 0:
            try:
                self.streaming_engine.feed(prebuffer_audio)
            except Exception as e:  # noqa: BLE001
                log.debug("prebuffer feed to streaming failed: %s", e)
        self._record_started_at = time.monotonic()
        self._elapsed_timer.start()
        self.tray.set_state(State.RECORDING)
        log.debug("toggle->ready %.0fms", (time.monotonic() - t_toggle) * 1000)

    def _stop_and_transcribe(self) -> None:
        if not self.state.try_transition(State.TRANSCRIBING):
            return
        self._t_stop_pressed = time.monotonic()
        log.info("RECORDING -> TRANSCRIBING")
        self._elapsed_timer.stop()
        try:
            self.streaming_engine.end_session()
        except Exception as e:  # noqa: BLE001
            log.warning("streaming-engine end_session failed: %s", e)
        audio = self.recorder.stop()

        if audio is None or len(audio) < MIN_AUDIO_SECONDS * SAMPLE_RATE:
            log.info("skip: audio too short")
            self._reset_to_idle()
            return

        self.popup.show_transcribing()
        self.tray.set_state(State.TRANSCRIBING)

        self._worker = TranscribeWorker(self.transcriber, audio)
        self._worker.done.connect(self._on_transcribed)
        self._worker.failed.connect(self._on_transcribe_failed)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _on_transcribed(self, text: str) -> None:
        t_done = time.monotonic()
        raw = text.strip()
        polished = polish(raw)
        log.info("transcript: %r", polished)
        log.debug("raw=%r polished=%r", raw, polished)
        if not polished:
            self._reset_to_idle()
            return

        self.state.try_transition(State.PASTING, force=True)
        log.info("TRANSCRIBING -> PASTING (target hwnd=%s)", self._paste_target_hwnd)
        self.popup.set_partial(polished, "")
        self.tray.set_state(State.PASTING)

        # Persist to history before pasting — if paste fails the user
        # can still find what they said in Settings → History.
        try:
            history.append(polished)
        except Exception as e:  # noqa: BLE001
            log.warning("history.append failed: %s", e)
        self.tray.refresh_menu()

        t_paste_start = time.monotonic()
        try:
            paste_text(
                polished,
                target_hwnd=self._paste_target_hwnd,
                auto_send=self.config.auto_send,
                preserve_clipboard=self.config.preserve_clipboard,
                paste_method=self.config.paste_method,
            )
        except Exception as e:  # noqa: BLE001
            log.error("paste failed: %s", e)
        t_end = time.monotonic()
        log.info(
            "timing: stop->transcribed %.0fms transcribed->paste %.0fms paste %.0fms total %.0fms",
            (t_done - self._t_stop_pressed) * 1000,
            (t_paste_start - t_done) * 1000,
            (t_end - t_paste_start) * 1000,
            (t_end - self._t_stop_pressed) * 1000,
        )
        self._reset_to_idle()

    def _on_transcribe_failed(self, msg: str) -> None:
        log.error("transcribe failed: %s", msg)
        self._reset_to_idle()

    def _reset_to_idle(self) -> None:
        self.state.try_transition(State.IDLE, force=True)
        log.debug("-> IDLE")
        # Release the reverse-PTT mute key so the call app un-mutes
        # the user. Idempotent: no-op if disarmed or not held.
        self.mute_key.release()
        self.popup.hide_popup()
        self.tray.set_state(State.IDLE)

    def _update_elapsed(self) -> None:
        elapsed = time.monotonic() - self._record_started_at
        self.popup.set_elapsed(elapsed)

    # ------------------------------------------------------------ settings

    def _ensure_settings_dialog(self) -> SettingsDialog:
        if self._settings_dialog is None:
            self._settings_dialog = SettingsDialog(
                config=self.config,
                on_config_changed=self._on_config_changed,
                on_hotkey_changed=self._on_hotkey_changed,
                on_quit=self._on_quit,
                app_icon=self._app_icon,
            )
        return self._settings_dialog

    def _on_open_settings(self) -> None:
        dlg = self._ensure_settings_dialog()
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _on_config_changed(self) -> None:
        """Settings tabs call this whenever the user changes anything.
        Persist + apply the hot-tunable knobs (language, initial prompt,
        mic device). Engine/model changes are persisted but only apply
        after a restart — that's the Engine tab's notice to the user.
        """
        try:
            self.config.save()
        except Exception as e:  # noqa: BLE001
            log.warning("could not save config: %s", e)
        # AudioRecorder picks up device on the next ``start()``.
        self.recorder.set_device(self.config.input_device_name)
        # Push hot-tunable knobs into the live transcriber.
        try:
            self.transcriber.set_runtime_config(
                language=self.config.language or None,
                initial_prompt=self.config.initial_prompt,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("transcriber.set_runtime_config failed: %s", e)
        # Re-arm (or disarm) the reverse-PTT mute key per the new config.
        if self.config.reverse_ptt_enabled and self.config.reverse_ptt_vk:
            self.mute_key.arm(self.config.reverse_ptt_vk)
        else:
            self.mute_key.disarm()

    def _on_hotkey_changed(self, vk: int) -> None:
        log.info("hotkey rebound to %s (vk=%#x)", vk_label(vk), vk)
        self.hotkey.set_vk(vk)
        self.tray.set_hotkey_label(vk_label(vk))

    # ------------------------------------------------------------------ exit
    def _on_quit(self) -> None:
        log.info("quit requested")
        # Belt-and-suspenders: make sure we don't exit with the mute
        # key still virtually held (would leave the user muted in
        # their call until they manually pressed it themselves).
        self.mute_key.release()
        self.hotkey.stop()
        self.foreground.stop()
        if self.recorder.is_recording():
            self.recorder.stop()
        self.recorder.close()
        try:
            self.streaming_engine.end_session()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.streaming_engine.shutdown()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.transcriber.close()
        except Exception:  # noqa: BLE001
            pass
        self.tray.stop()
        self.qapp.quit()

    def run(self) -> int:
        return self.qapp.exec()
