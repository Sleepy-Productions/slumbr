"""QApplication wiring — tray + popup + hotkey + hub.

Threading model
---------------
- Qt main thread owns the state machine, popup, paste, engine-call sites,
  and the hub window.
- The pynput hook thread (configurable single-key filter) emits
  `bridge.toggle` via signal, which lands on the main thread via
  QueuedConnection.
- The pystray thread (tray loop) emits `bridge.toggle`,
  `bridge.open_settings`, or `bridge.quit_requested` the same way.
- Audio is captured by sounddevice's PortAudio thread — that callback
  emits `bridge.audio_chunk` (queued) so the popup's visualizer can
  redraw on the main thread without touching Qt widgets from PortAudio.
- Transcription runs inside `TranscribeWorker` (a QThread, see
  `slumbr/stt/worker.py`). `done` and `failed` auto-queue back to the
  main thread.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .audio.capture import SAMPLE_RATE, AudioRecorder
from .config import SlumbrConfig
from .input.foreground import ForegroundTracker
from .input.hotkey import Hotkey
from .input.keymap import vk_label
from .input.paste import paste_text
from .polish import polish
from .state import State, StateMachine
from .stt.engine import WhisperEngine
from .stt.streaming_engine import StreamingASREngine
from .stt.worker import TranscribeWorker
from .ui.main_window import MainWindow
from .ui.popup import RecordingPopup
from .ui.tray import SlumbrTray

log = logging.getLogger(__name__)

_ASSET_DIR = Path(__file__).resolve().parent / "assets"
_ICON_PATH = _ASSET_DIR / "icon.ico"

DEFAULT_DEVICE = "cuda"
MIN_AUDIO_SECONDS = 0.3


class _Bridge(QObject):
    toggle = Signal(str)  # source tag: "hotkey", "tray", "main_window"
    open_settings = Signal()
    show_main_window = Signal()
    quit_requested = Signal()
    audio_chunk = Signal(object)  # numpy ndarray payload


class SlumbrApp:
    def __init__(self) -> None:
        # Engine MUST load before QApplication. PySide6's Qt bootstrap
        # perturbs the Windows DLL search path in a way that breaks
        # CTranslate2's lazy cuBLAS/cuDNN load — the first
        # `WhisperModel(...)` after `QApplication()` crashes natively
        # (exit code 5, no traceback). Once the model is loaded, later
        # transcribes are fine because the DLLs are resolved into the
        # process. The order also matters at import time, hence
        # `slumbr/__init__.py` doing a preload of `ctranslate2`.
        # Load config BEFORE the engine so we can hand it model+precision
        # and runtime knobs in one shot. language + initial_prompt are
        # hot-tunable via Settings; model_size + compute_type take effect
        # only on the next launch (faster-whisper has no hot-swap).
        _early_config = SlumbrConfig.load()
        log.info(
            "loading Whisper model=%r compute_type=%r (first launch downloads it)",
            _early_config.model_size,
            _early_config.compute_type,
        )
        self.engine = WhisperEngine(
            model_size=_early_config.model_size,
            device=DEFAULT_DEVICE,
            compute_type=_early_config.compute_type,
            language=_early_config.language or None,
            initial_prompt=_early_config.initial_prompt,
        )
        self.engine.warm_up()

        # Streaming engine for live popup partials. CPU-only by design (no
        # GPU contention with Whisper) — Moonshine base int8 + Silero VAD
        # + LocalAgreement-2 commit. When the user has enabled the
        # experimental leading edge, a sherpa-onnx streaming Zipformer
        # rides alongside for the visible tentative tail.
        self.streaming_engine = StreamingASREngine(
            enable_streaming_leading_edge=_early_config.streaming_visual_leading_edge,
        )

        self.qapp = QApplication(sys.argv)
        self.qapp.setQuitOnLastWindowClosed(False)
        self._app_icon: QIcon | None = None
        if _ICON_PATH.is_file():
            self._app_icon = QIcon(str(_ICON_PATH))
            self.qapp.setWindowIcon(self._app_icon)

        # Re-use the config we loaded for the engine instead of reading the
        # file twice — keeps startup tight and avoids drift if the file
        # changes between the two loads.
        self.config = _early_config

        self.state = StateMachine()
        self.popup = RecordingPopup()
        self.foreground = ForegroundTracker()
        self.foreground.start()
        self._paste_target_hwnd: int | None = None

        self.bridge = _Bridge()
        self.bridge.toggle.connect(self._on_toggle, Qt.QueuedConnection)
        self.bridge.open_settings.connect(self._on_open_settings, Qt.QueuedConnection)
        self.bridge.show_main_window.connect(self._on_show_main_window, Qt.QueuedConnection)
        self.bridge.quit_requested.connect(self._on_quit, Qt.QueuedConnection)
        self.bridge.audio_chunk.connect(self._on_audio_chunk, Qt.QueuedConnection)

        # Main window is the persistent control hub. Construct here so tray
        # callbacks can reach it; show after tray/hotkey are wired below.
        self.main_window = MainWindow(
            config=self.config,
            on_toggle=lambda: self.bridge.toggle.emit("main_window"),
            on_quit=self.bridge.quit_requested.emit,
            on_config_changed=self._on_config_changed,
            on_hotkey_changed=self._on_hotkey_changed,
            app_icon=self._app_icon,
        )

        # Recorder wires on_chunk -> bridge.audio_chunk.emit; that signal,
        # connected with QueuedConnection, marshals the numpy array onto
        # the Qt main thread where the visualizer can safely consume it.
        self.recorder = AudioRecorder(
            device=self.config.input_device_name,
            on_chunk=self._on_audio_thread_chunk,
        )

        self.tray = SlumbrTray(
            on_toggle=lambda: self.bridge.toggle.emit("tray"),
            on_show_window=self.bridge.show_main_window.emit,
            on_settings=self.bridge.open_settings.emit,
            on_quit=self.bridge.quit_requested.emit,
            hotkey_label=vk_label(self.config.hotkey_vk),
        )
        self.tray.start()

        # Open the hub on launch so the user has somewhere to land.
        self.main_window.show()

        self.hotkey = Hotkey(
            vk=self.config.hotkey_vk,
            on_press=lambda: self.bridge.toggle.emit("hotkey"),
        )
        self.hotkey.start()

        self._elapsed_timer = QTimer()
        self._elapsed_timer.setInterval(200)
        self._elapsed_timer.timeout.connect(self._update_elapsed)
        self._record_started_at = 0.0

        self._worker: TranscribeWorker | None = None

        # Wall-clock anchors for end-to-end latency tracing. Set on toggle
        # and consulted at each pipeline boundary so the log reads like
        # `+312ms transcribe → +18ms paste`.
        self._t_stop_pressed: float = 0.0

        log.info(
            "ready. Tap %s to start/stop. Quit from the tray.",
            vk_label(self.config.hotkey_vk),
        )

    # ------------------------------------------------------- audio chunk hop
    def _on_audio_thread_chunk(self, samples: np.ndarray) -> None:
        # Called on PortAudio thread. Marshal onto Qt main thread.
        # `.copy()` because the array is owned by PortAudio's ring buffer.
        self.bridge.audio_chunk.emit(samples.copy())

    def _on_audio_chunk(self, samples) -> None:
        # On Qt main thread. Two consumers of the chunk:
        # 1) the popup visualizer bars (cheap repaint)
        # 2) the streaming ASR engine (sherpa-onnx, ~15 ms decode per chunk)
        self.popup.push_samples(samples)
        if self.state.state is State.RECORDING:
            try:
                partial = self.streaming_engine.feed(samples)
            except Exception as e:  # noqa: BLE001
                log.warning("streaming-engine feed failed: %s", e)
                return
            if partial.committed or partial.tentative:
                # Moonshine + online-punct produce properly cased +
                # punctuated text; LocalAgreement-2 in the engine has
                # split it into (committed, tentative) — the popup draws
                # them at different opacities.
                self.popup.set_partial(partial.committed, partial.tentative)

    # --------------------------------------------------------------- toggle
    def _on_toggle(self, source: str = "?") -> None:
        current = self.state.state
        log.debug("toggle source=%s state=%s", source, current.value)
        if current is State.IDLE:
            self._start_recording()
        elif current is State.RECORDING:
            self._stop_and_transcribe()
        else:
            log.debug("ignore toggle during %s", current.value)

    def _start_recording(self) -> None:
        if not self.state.try_transition(State.RECORDING):
            return
        t_toggle = time.monotonic()
        self._paste_target_hwnd = self.foreground.last_hwnd()
        log.info("IDLE -> RECORDING (target hwnd=%s)", self._paste_target_hwnd)
        # Show the popup BEFORE arming the recorder. recorder.start() is
        # already a cheap flag-flip (stream is always open) but ordering
        # this way means the popup paints first if anything slips.
        self.popup.show_recording()
        try:
            self.recorder.start()
        except Exception as e:  # noqa: BLE001
            log.error("could not start recording: %s", e)
            self._reset_to_idle()
            return
        # Begin a fresh streaming session for this utterance — popup partials
        # will start landing as soon as audio arrives (~300 ms latency).
        self.streaming_engine.start_session()
        # Seed the streaming engine with the ~500 ms of audio captured
        # before the hotkey was pressed. Most of it is ambient silence —
        # VAD will discard those frames — but if the user started
        # speaking just before tapping the hotkey, the prebuffer catches
        # the first syllable and the popup gets a head start instead of
        # waiting for new audio to accumulate.
        prebuffer_audio = self.recorder.snapshot()
        if prebuffer_audio is not None and prebuffer_audio.size > 0:
            try:
                self.streaming_engine.feed(prebuffer_audio)
            except Exception as e:  # noqa: BLE001
                log.debug("prebuffer feed to streaming failed: %s", e)
        self._record_started_at = time.monotonic()
        self._elapsed_timer.start()
        self.tray.set_state(State.RECORDING)
        self.main_window.set_state(State.RECORDING)
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

        # NOTE: we used to call restore_foreground here as a head start for
        # DWM, then have paste_text skip its own restore. That broke VS Code
        # and other Electron targets — if the early call silently fails
        # (Windows foreground-locking rules), we'd burn input-rights and
        # the late restore at paste time would also fail. Doing the restore
        # exactly once, at paste time, is the proven reliable path.

        self.popup.show_transcribing()
        self.tray.set_state(State.TRANSCRIBING)
        self.main_window.set_state(State.TRANSCRIBING)

        self._worker = TranscribeWorker(self.engine, audio)
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
        # Replace the popup's streaming partial with Whisper's polished
        # text *before* pasting. Moonshine is less accurate than Whisper,
        # so without this the popup ends on a wrong guess while the
        # target window receives the correct paste — visually disorienting.
        # All chars render as committed (full opacity); the existing per-
        # char fade smoothly retargets without flashing.
        self.popup.set_partial(polished, "")
        self.tray.set_state(State.PASTING)
        self.main_window.set_state(State.PASTING)
        self.main_window.set_last_transcript(polished)
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
        # Pipeline timing summary: each segment in ms, plus end-to-end
        # from the user releasing Caps Lock to text in the target window.
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
        self.popup.hide_popup()
        self.tray.set_state(State.IDLE)
        self.main_window.set_state(State.IDLE)

    def _update_elapsed(self) -> None:
        elapsed = time.monotonic() - self._record_started_at
        self.popup.set_elapsed(elapsed)

    # ------------------------------------------------------------ settings
    def _on_open_settings(self) -> None:
        # Tray "Settings…" routes to the hub's Voice panel.
        self._on_show_main_window()
        self.main_window.jump_to_voice()

    def _on_config_changed(self) -> None:
        """Hub panels (Voice / Behavior / Shortcuts close-to-tray) call this
        whenever the user changes anything. Persist + apply hot-tunable knobs.
        """
        try:
            self.config.save()
        except Exception as e:  # noqa: BLE001
            log.warning("could not save config: %s", e)
        # AudioRecorder picks up device on the next `start()`.
        self.recorder.set_device(self.config.input_device_name)
        # Push language / initial_prompt into the engine so the very next
        # transcribe sees them — no model reload.
        self.engine.set_runtime_config(
            language=self.config.language or None,
            initial_prompt=self.config.initial_prompt,
        )

    def _on_hotkey_changed(self, vk: int) -> None:
        log.info("hotkey rebound to %s (vk=%#x)", vk_label(vk), vk)
        self.hotkey.set_vk(vk)
        self.tray.set_hotkey_label(vk_label(vk))

    # -------------------------------------------------------- main window
    def _on_show_main_window(self) -> None:
        # The window may have been hidden via close-to-tray. show() alone
        # doesn't beat the taskbar order when another app is foreground, so
        # also restore + activate + raise.
        self.main_window.show()
        self.main_window.setWindowState(self.main_window.windowState() & ~Qt.WindowMinimized)
        self.main_window.raise_()
        self.main_window.activateWindow()

    # ------------------------------------------------------------------ exit
    def _on_quit(self) -> None:
        log.info("quit requested")
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
        self.tray.stop()
        self.qapp.quit()

    def run(self) -> int:
        return self.qapp.exec()
