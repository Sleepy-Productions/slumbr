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
from PySide6.QtCore import QFileSystemWatcher, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QDialog

from . import history, session_logs
from .audio.capture import SAMPLE_RATE, AudioRecorder
from .audio.mirror import MicMirror
from .bootstrap.install import relaunch_slumbr
from .config import SlumbrConfig
from .input.foreground import ForegroundTracker
from .input.hotkey import Hotkey
from .input.keymap import combo_label
from .input.mute_key import MuteKeyController
from .input.paste import paste_text
from .polish import polish
from .state import State, StateMachine
from .stt.protocol import Transcriber
from .stt.worker import TranscribeWorker
from .ui.popup import RecordingPopup
from .ui.preparing import prepare_engines
from .ui.settings_dialog import SettingsDialog
from .ui.setup_wizard import SetupWizard
from .ui.tray import SlumbrTray

log = logging.getLogger(__name__)

_ASSET_DIR = Path(__file__).resolve().parent / "assets"
_ICON_PATH = _ASSET_DIR / "icon.ico"

MIN_AUDIO_SECONDS = 0.3


class _Bridge(QObject):
    toggle = Signal(str)  # source tag: "hotkey", "tray"
    open_settings = Signal()
    quit_requested = Signal()
    restart_requested = Signal()
    quick_toggle = Signal(str)  # SlumbrConfig field name (bool field)
    audio_chunk = Signal(object)  # numpy ndarray payload


class SlumbrApp:
    def __init__(self) -> None:
        # ----- Taskbar/alt-tab identity. Must run before QApplication, else
        # Windows groups the app (when run via pythonw) under the generic
        # Python icon instead of Slumbr's. Use the ONE canonical AUMID from
        # winident — it MUST match the value stamped on the shortcuts
        # (SleepyDev.Slumbr), or the pinned launcher and the live window won't
        # unify into a single taskbar button. Idempotent; no-op off Windows.
        from .winident import set_process_app_id

        set_process_app_id()

        # ----- QApplication first, so the wizard can use Qt widgets.
        self.qapp = QApplication(sys.argv)
        self.qapp.setQuitOnLastWindowClosed(False)
        # ----- Register house fonts (Inter body / Sora display) app-wide, right
        # after QApplication exists. Defensive no-op → system font on failure.
        from .theme import load_app_fonts

        load_app_fonts(self.qapp)
        # Black palette so non-stylesheet surfaces (scroll viewports, etc.)
        # don't fall back to the OS-default grey behind the dark UI.
        from .theme import apply_dark_palette

        apply_dark_palette(self.qapp)

        # ----- Config (with legacy → BackendConfig migration).
        self.config = SlumbrConfig.load()

        # ----- History is in-memory and ephemeral — nothing about dictations is
        # written to disk, so there's nothing to recover and nothing to clean up
        # but a possibly-stale single-instance lock from an unclean prior exit.
        # Start fresh; ``begin()`` drops this session's marker after the wizard.
        self._restarting = False
        history.clear_memory()  # start with empty in-memory list; configure() loads disk if opted in
        session_logs.reset()
        # Apply the saved history-persistence preference. OFF by default keeps
        # the ephemeral in-memory behavior; ON backfills the live view from the
        # on-disk store so previous sessions are visible immediately.
        history.configure(self.config.persist_history)

        # ----- Taskbar / window icon = the moon-v2 brand mark in the FIXED
        # monochrome brand color (branding.LOGO_COLOR = white). It does NOT
        # follow the user's accent — the logo is the brand mark, the accent is
        # "your color" for the chrome (tray dot, visualizer). Keep it that way:
        # a pink/tinted icon is a bug, not a feature. Falls back to the static
        # brand .ico if the render fails.
        self._app_icon: QIcon | None = self._build_app_icon()
        if self._app_icon is not None:
            self.qapp.setWindowIcon(self._app_icon)

        # ----- First-launch wizard if backend isn't set yet.
        self._show_settings_on_first_run = False
        if self.config.backend is None:
            log.info("first launch: showing setup wizard")
            wizard = SetupWizard(self.config, app_icon=self._app_icon)
            result = wizard.exec()
            if result != QDialog.Accepted or self.config.backend is None:
                log.info("setup wizard cancelled — exiting")
                raise SystemExit(0)
            # Wizard just completed → genuine first launch. Pop Settings once
            # the app is up so a new user sees what they can tune.
            self._show_settings_on_first_run = True

        log.info(
            "selected backend=%s model=%s",
            self.config.backend.name,
            self.config.backend.model,
        )

        # Drop this session's running marker now that we're committed to
        # running (past the wizard) — its absence next launch == clean exit.
        session_logs.begin()

        # ----- Reopen-from-taskbar bridge. Slumbr lives in the tray, so when
        # the Settings window is closed there's no visible window for Windows to
        # re-activate when the pinned taskbar icon is clicked again. That second
        # launch instead drops a "show.request" marker (see __main__) and exits;
        # we watch the session dir for it and surface Settings — so clicking the
        # pinned icon ALWAYS brings Slumbr forward, whether it was running or not.
        self._reopen_watcher = QFileSystemWatcher()
        try:
            self._reopen_watcher.addPath(str(session_logs.session_dir()))
            self._reopen_watcher.directoryChanged.connect(self._on_session_dir_changed)
        except Exception:  # noqa: BLE001
            log.warning("reopen watcher setup failed (non-fatal)", exc_info=True)

        # ----- Transcriber (primary STT) + streaming engine (live popup
        # partials, always Moonshine on CPU). Built on a worker thread
        # behind a "Preparing Slumbr" progress dialog so the first-run model
        # download shows feedback instead of a silent multi-minute freeze,
        # and a download failure surfaces a clear error (see preparing.py).
        self.transcriber: Transcriber
        self.transcriber, self.streaming_engine = prepare_engines(self.config, self._app_icon)

        # ----- App state + popup + foreground tracker.
        self.state = StateMachine()
        self.popup = RecordingPopup()
        self.popup.set_compact(self.config.compact_popup)
        self.popup.set_follow_cursor(self.config.popup_follow_cursor)
        self.popup.set_accent(self.config.accent_color)
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
        self.bridge.restart_requested.connect(self._on_restart, Qt.QueuedConnection)
        self.bridge.quick_toggle.connect(self._on_quick_toggle, Qt.QueuedConnection)
        self.bridge.audio_chunk.connect(self._on_audio_chunk, Qt.QueuedConnection)

        # ----- Audio recorder.
        # Two callbacks:
        #   on_chunk            : only fires during dictation, feeds the
        #                         popup visualizer + streaming engine.
        #                         (Goes through bridge → Qt main thread.)
        #   on_chunk_continuous : fires on every chunk regardless of state,
        #                         feeds the MicMirror so call apps reading
        #                         the virtual cable always hear the user
        #                         (except during dictation when MicMirror
        #                         is internally muted).
        # Init the mic-mirror handle BEFORE the recorder: the recorder's
        # continuous callback can fire the instant its stream opens, and
        # ``_on_audio_continuous`` reads ``self.mic_mirror`` — assigning it
        # here closes a startup race that logged a harmless AttributeError on
        # every launch. ``_try_open_mic_mirror()`` populates it for real below.
        self.mic_mirror: MicMirror | None = None
        self.recorder = AudioRecorder(
            device=self.config.input_device_name,
            on_chunk=self._on_audio_thread_chunk,
            on_chunk_continuous=self._on_audio_continuous,
        )

        # ----- Tray.
        self.tray = SlumbrTray(
            on_toggle=lambda: self.bridge.toggle.emit("tray"),
            on_settings=self.bridge.open_settings.emit,
            on_quit=self.bridge.quit_requested.emit,
            on_restart=self.bridge.restart_requested.emit,
            config=self.config,
            on_quick_toggle=self.bridge.quick_toggle.emit,
            hotkey_label=combo_label(self.config.hotkey_vks),
        )
        self.tray.start()

        # ----- Hotkey hook (low-level WH_KEYBOARD_LL). Accepts a 1–4 key combo.
        self.hotkey = Hotkey(
            vks=self.config.hotkey_vks,
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

        # ----- Virtual mic routing — universal reverse-PTT path. When
        # active, Slumbr passes the real mic to a virtual cable device
        # that call apps read from. The cable is fed silence during
        # dictation so other apps hear nothing while Slumbr's own
        # capture stream keeps producing transcripts. (The handle itself is
        # initialized to None up by the recorder to avoid a startup race.)
        self._try_open_mic_mirror()

        # ----- Settings dialog is built lazily on first open so startup
        # doesn't pay the cost for users who never touch it.
        self._settings_dialog: SettingsDialog | None = None

        log.info(
            "ready. Tap %s to start/stop. Quit from the tray.",
            combo_label(self.config.hotkey_vks),
        )

    # ------------------------------------------------------- audio chunk hop
    def _on_audio_continuous(self, samples: np.ndarray) -> None:
        """Fires on every PortAudio callback regardless of recording
        state. Sole responsibility: keep the MicMirror fed so call
        apps reading the virtual cable always hear the user (except
        during dictation when MicMirror itself goes silent). Runs on
        the PortAudio input thread — must stay fast.
        """
        if self.mic_mirror is not None:
            self.mic_mirror.push(samples)

    def _on_audio_thread_chunk(self, samples: np.ndarray) -> None:
        # Called on PortAudio input thread, only during dictation
        # (gated by AudioRecorder's _saving flag). Marshals a copy
        # onto the Qt main thread for the popup visualizer +
        # streaming-engine partials.
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
        # Same for the virtual-mic mirror: cut the passthrough so call
        # apps reading the virtual cable hear silence while Slumbr
        # records normally.
        if self.mic_mirror is not None:
            self.mic_mirror.set_muted(True)
        self.popup.show_recording()
        # If the input stream never opened (device unplugged/renamed/in use),
        # recording would silently capture nothing — tell the user instead.
        if not self.recorder.has_stream():
            log.error("no input stream — microphone unavailable")
            self.tray.notify(
                "Microphone unavailable — check your input device in Settings → Voice.",
            )
            self._reset_to_idle(error=True)
            return
        try:
            self.recorder.start()
        except Exception as e:  # noqa: BLE001
            log.error("could not start recording: %s", e)
            self.tray.notify("Couldn't start recording — see logs.")
            self._reset_to_idle(error=True)
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
        polished = polish(
            raw,
            replacements=self.config.word_replacements,
            strip_filler=True,  # always strip end-of-clip hallucinations — never a choice
        )
        # Log only the LENGTH, never the transcript text — Slumbr keeps no
        # record of what you dictated, on disk or in the log.
        log.info("transcript ready (%d chars)", len(polished))
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
        paste_ok = True
        try:
            paste_text(
                polished,
                target_hwnd=self._paste_target_hwnd,
                auto_send=self.config.auto_send,
                # "Keep transcript on clipboard" = don't restore the old clipboard.
                preserve_clipboard=not self.config.keep_transcript_on_clipboard,
                paste_method=self.config.paste_method,
            )
        except Exception as e:  # noqa: BLE001
            log.error("paste failed: %s", e)
            paste_ok = False
            # The popup flashes red, but the user may not be looking — a tray
            # balloon makes the failure unmissable and points at the recovery.
            self.tray.notify("Couldn't paste — your text is saved in History.")
        t_end = time.monotonic()
        log.info(
            "timing: stop->transcribed %.0fms transcribed->paste %.0fms paste %.0fms total %.0fms",
            (t_done - self._t_stop_pressed) * 1000,
            (t_paste_start - t_done) * 1000,
            (t_end - t_paste_start) * 1000,
            (t_end - self._t_stop_pressed) * 1000,
        )
        self._reset_to_idle(sent=paste_ok)

    def _on_transcribe_failed(self, msg: str) -> None:
        log.error("transcribe failed: %s", msg)
        self.tray.notify("Couldn't transcribe that — see the log for details.")
        self._reset_to_idle(error=True)

    def _reset_to_idle(self, *, sent: bool = False, error: bool = False) -> None:
        self.state.try_transition(State.IDLE, force=True)
        log.debug("-> IDLE")
        # Release the reverse-PTT mute key so the call app un-mutes
        # the user. Idempotent: no-op if disarmed or not held.
        self.mute_key.release()
        # Re-open the virtual-mic passthrough so call apps hear the
        # user again. Idempotent: no-op when the mirror is disabled
        # or already unmuted.
        if self.mic_mirror is not None:
            self.mic_mirror.set_muted(False)
        # Outcome cue: green "✓ Sent" on a successful paste, red "✗ Failed"
        # on an error (the tray notification carries the detail); otherwise
        # just hide.
        if error:
            self.popup.flash_error()
        elif sent:
            self.popup.flash_sent()
        else:
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
                on_restart=self.bridge.restart_requested.emit,
                app_icon=self._app_icon,
            )
        return self._settings_dialog

    def _on_open_settings(self) -> None:
        dlg = self._ensure_settings_dialog()
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _on_session_dir_changed(self, _path: str) -> None:
        """A second launch (pinned-icon click while Slumbr is in the tray) drops
        a show-request marker here — surface Settings when it appears. Re-arm the
        watch afterwards: QFileSystemWatcher can drop a path after some change
        events, and we need every future reopen click to keep working."""
        if session_logs.consume_show_request():
            self._open_settings_on_launch()
        d = str(session_logs.session_dir())
        if d not in self._reopen_watcher.directories():
            self._reopen_watcher.addPath(d)

    def _open_settings_on_launch(self) -> None:
        """Open Settings on launch so Slumbr shows a window (not just a tray
        icon) — centered at its comfortable default size. NOT maximized: the
        layout is sidebar-left / content-left, so maximizing on a wide monitor
        leaves a large dead strip on the right. Centered reads cleaner."""
        from PySide6.QtGui import QGuiApplication

        dlg = self._ensure_settings_dialog()
        dlg.showNormal()
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            frame = dlg.frameGeometry()
            frame.moveCenter(screen.availableGeometry().center())
            dlg.move(frame.topLeft())
        dlg.raise_()
        dlg.activateWindow()

    def _build_app_icon(self) -> QIcon | None:
        """Window/taskbar icon: the FIXED monochrome moon-v2 brand mark — the
        symbol stays black-and-white, it doesn't follow the accent. Falls back
        to the static .ico, then None."""
        try:
            from .branding import LOGO_COLOR
            from .ui.tabs._widgets import glyph_icon

            return glyph_icon(LOGO_COLOR, 256)
        except Exception:  # noqa: BLE001
            log.exception("brand app-icon failed; using static .ico")
            return QIcon(str(_ICON_PATH)) if _ICON_PATH.is_file() else None

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
        # Reconcile the virtual-mic mirror with the current settings —
        # hot-start, hot-stop, or hot-swap-device without restart.
        self._reconcile_mic_mirror()
        # Popup look (compact vs full) + cursor-follow.
        self.popup.set_compact(self.config.compact_popup)
        self.popup.set_follow_cursor(self.config.popup_follow_cursor)
        self.popup.set_accent(self.config.accent_color)
        self.tray.set_accent(self.config.accent_color)
        # (The window/taskbar icon is a fixed monochrome brand mark — it does
        # NOT track the accent, so nothing to rebuild here.)
        # Refresh the tray menu so quick-toggle checkmarks reflect new state.
        self.tray.refresh_menu()

    # ----------------------------------------------------- mic mirror

    def _try_open_mic_mirror(self) -> None:
        """Open the virtual-mic mirror per the current config, if any.

        If routing is enabled but no device is set (e.g. user ticked the
        checkbox before the Behavior tab's auto-preselect was persisted),
        we auto-pick the first detected cable and save the choice. This
        keeps the feature functional even on a half-configured state.
        """
        if not self.config.mic_routing_enabled:
            return
        from .audio.mirror import find_virtual_cables  # noqa: PLC0415

        device = self.config.mic_routing_device_name

        # No device saved yet → auto-pick the best detected cable.
        if not device:
            cables = find_virtual_cables()
            if not cables:
                log.info(
                    "mic_routing enabled but no virtual cable detected — "
                    "skipping mirror. Install VB-Cable via Settings → Behavior."
                )
                return
            device = cables[0][1]
            self._save_mic_routing_device(device)
            log.info("mic_routing auto-picked device: %r", device)

        if self._open_mic_mirror_device(device):
            return

        # The configured device failed (e.g. a WDM-KS-only "cable" that
        # can't do blocking writes, or one that was uninstalled). Rather
        # than stay dead until the user notices, fall forward to the best
        # *working* cable detection offers — and persist the switch so we
        # don't fail the same way next launch.
        alternatives = [c for c in find_virtual_cables() if c[1] != device]
        if alternatives:
            alt = alternatives[0][1]
            log.warning("mic routing device %r unusable — falling forward to %r", device, alt)
            if self._open_mic_mirror_device(alt):
                self._save_mic_routing_device(alt)
                return
        log.warning(
            "could not open any usable mic mirror (configured=%r); routing "
            "disabled until next config change",
            device,
        )
        self.mic_mirror = None
        self.tray.notify(
            "Virtual mic routing is on but no usable cable was found — check Settings → Behavior.",
        )

    def _open_mic_mirror_device(self, device: str) -> bool:
        """Try to open + start the mirror on ``device``. Returns success."""
        try:
            self.mic_mirror = MicMirror(device, samplerate=SAMPLE_RATE, channels=1)
            self.mic_mirror.start()
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("could not open mic mirror for %r (%s)", device, e)
            self.mic_mirror = None
            return False

    def _save_mic_routing_device(self, device: str) -> None:
        self.config.mic_routing_device_name = device
        try:
            self.config.save()
        except Exception as e:  # noqa: BLE001
            log.warning("config save during mic_routing update failed: %s", e)

    def _reconcile_mic_mirror(self) -> None:
        """Hot-reconcile the running mirror with the current config.

        Transitions handled:
          - disabled → enabled : open + start a new mirror (with auto-pick
                                 if the user toggled the checkbox without
                                 explicitly picking a device)
          - enabled → disabled : stop + drop the current mirror
          - device changed     : stop the old mirror, open a new one
        """
        if not self.config.mic_routing_enabled:
            if self.mic_mirror is not None:
                self.mic_mirror.stop()
                self.mic_mirror = None
            return

        # Routing is enabled. If we have no mirror yet (just-enabled or
        # crashed earlier), let ``_try_open_mic_mirror`` handle the
        # auto-pick + open path.
        if self.mic_mirror is None:
            self._try_open_mic_mirror()
            return

        # The stream may have closed itself inside push() after a PortAudioError
        # (e.g. VB-Cable uninstalled or default device switched). In that case
        # the handle is non-None but is_running is False — reopen it.
        if not self.mic_mirror.is_running:
            log.info("mic_mirror stream died (device lost?); reopening")
            self.mic_mirror.stop()  # idempotent cleanup
            self.mic_mirror = None
            self._try_open_mic_mirror()
            return

        # Already running — check whether the user picked a different
        # device. Compare by name (MicMirror stores the original name
        # passed in; the resolved int index isn't comparable to config).
        cur_name = getattr(self.mic_mirror, "_device_name", "")
        target = self.config.mic_routing_device_name
        if target and cur_name != target:
            self.mic_mirror.stop()
            self.mic_mirror = None
            self._try_open_mic_mirror()

    def _on_hotkey_changed(self, vks: list[int]) -> None:
        if not vks:
            return
        self.config.hotkey_vks = list(vks)
        self.config.hotkey_vk = vks[-1]  # keep the legacy single field in sync
        label = combo_label(vks)
        log.info("hotkey rebound to %s", label)
        self.hotkey.set_vks(vks)
        self.tray.set_hotkey_label(label)
        # Persist immediately — rebinding should survive a restart (the old
        # single-key path never saved, so a rebind silently reverted).
        try:
            self.config.save()
        except Exception as e:  # noqa: BLE001
            log.warning("could not save hotkey rebind: %s", e)

    # --------------------------------------------------------- quick toggles
    def _on_quick_toggle(self, field_name: str) -> None:
        """Tray menu quick-toggle for a bool field on SlumbrConfig.

        Flips the named field + reuses ``_on_config_changed`` so the
        normal save + reconciliation path fires (recorder device
        re-pick, mic mirror open/close, mute key re-arm, popup look,
        tray menu refresh).
        """
        if not hasattr(self.config, field_name):
            log.warning("unknown quick-toggle field: %r", field_name)
            return
        current = getattr(self.config, field_name)
        if not isinstance(current, bool):
            log.warning("quick-toggle target %r is not a bool", field_name)
            return
        setattr(self.config, field_name, not current)
        log.info("quick-toggle %s -> %s", field_name, not current)
        self._on_config_changed()

    # ------------------------------------------------------------- restart
    def _on_restart(self) -> None:
        """Spawn a fresh Slumbr in a detached process, then quit this one.

        Spawn-first / quit-second so a stuck shutdown still gets the
        replacement running. Brief overlap (~3–5 s while the new
        instance loads the model) is acceptable — both share VRAM
        cleanly and pynput's hook reshuffles when the old process
        releases it.

        Use case: post-install relaunch after Settings → Behavior →
        Install VB-Cable (driver only appears in sounddevice's device
        list after a process restart following the Windows reboot, but
        a Slumbr-internal restart also picks up config-only changes
        that aren't hot-applied — switching backend, etc.).
        """
        log.info("restart requested")
        # Clean session boundary: wipe the session now so the relaunched
        # instance starts fresh and doesn't read this exit as a crash. The
        # flag makes _on_quit skip its own session cleanup (the new instance
        # owns the session from here).
        session_logs.end()
        history.clear_memory()  # in-memory only — keep the persisted store
        self._restarting = True
        relaunch_slumbr()
        self._on_quit()

    # ------------------------------------------------------------------ exit
    def _on_quit(self) -> None:
        log.info("quit requested")
        # Belt-and-suspenders: make sure we don't exit with the mute
        # key still virtually held (would leave the user muted in
        # their call until they manually pressed it themselves).
        self.mute_key.release()
        # Same for the mirror: a quit-during-RECORDING would leave the
        # cable feeding silence to call apps forever; un-mute and stop
        # cleanly.
        if self.mic_mirror is not None:
            self.mic_mirror.set_muted(False)
            self.mic_mirror.stop()
            self.mic_mirror = None
        self.hotkey.stop()
        self.foreground.stop()
        if self.recorder.is_recording():
            self.recorder.stop()
        self.recorder.close()
        # Wait for any in-flight TranscribeWorker to finish before closing the
        # backend. Closing the backend (which nulls the model handle) while the
        # worker is mid-transcribe is a native use-after-free that can silently
        # crash CTranslate2 / ONNX Runtime instead of raising a clean exception.
        # We bound the wait to 5 s so a hung/stuck decode doesn't freeze quit.
        if self._worker is not None and self._worker.isRunning():
            log.info("quit: waiting for in-flight transcription worker (max 5 s)")
            finished = self._worker.wait(5000)  # ms
            if not finished:
                log.warning("quit: worker did not finish in time; proceeding anyway")
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
        # Session-scoped cleanup: a clean exit wipes the temporary session logs
        # and the live history (reset every launch). Skipped on restart — the
        # handoff in _on_restart already did it and owns the next session.
        if not getattr(self, "_restarting", False):
            session_logs.end()
            history.clear_memory()  # in-memory only — keep the persisted store
        self.tray.stop()
        self.qapp.quit()

    def run(self) -> int:
        # Surface Settings (centered) shortly after boot so launching Slumbr
        # always shows a window, not just a tray icon. 800 ms lets boot settle.
        QTimer.singleShot(800, self._open_settings_on_launch)
        return self.qapp.exec()
