"""Slumbr Phase 1 — headless MVP.

Wires global hotkey -> audio capture -> Whisper transcription -> clipboard
paste. No UI yet; state transitions are logged to stdout. Phase 2 adds the
PySide6 recording popup with the waveform visualizer.
"""

from __future__ import annotations

import queue
import signal
import sys
import threading

from .audio.capture import SAMPLE_RATE, AudioRecorder
from .input.hotkey import HotkeyListener
from .input.paste import paste_text
from .state import State, StateMachine
from .stt.engine import TranscriptionError, WhisperEngine

# Phase 1 has no settings UI — values here are the defaults that will move
# into config.json in Phase 3.
DEFAULT_HOTKEY = "<ctrl>+<alt>+<space>"
DEFAULT_MODEL_SIZE = "large-v3-turbo"
DEFAULT_DEVICE = "cuda"
DEFAULT_COMPUTE_TYPE = "int8"
MIN_AUDIO_SECONDS = 0.3  # Anything shorter is treated as an accidental press.


def main() -> int:
    print("=" * 60)
    print("Slumbr (Phase 1 — headless MVP)")
    print("=" * 60)

    engine = WhisperEngine(
        model_size=DEFAULT_MODEL_SIZE,
        device=DEFAULT_DEVICE,
        compute_type=DEFAULT_COMPUTE_TYPE,
    )
    engine.warm_up()

    state = StateMachine()
    recorder = AudioRecorder()
    events: queue.Queue[str] = queue.Queue()
    shutdown = threading.Event()

    def on_hotkey() -> None:
        # Runs on pynput's input thread — keep this trivial.
        events.put("toggle")

    listener = HotkeyListener(combo=DEFAULT_HOTKEY, on_press=on_hotkey)
    listener.start()

    def on_sigint(_signum, _frame) -> None:
        events.put("quit")

    signal.signal(signal.SIGINT, on_sigint)

    print(f"\nReady. Press {DEFAULT_HOTKEY} to start/stop dictating. Ctrl+C here to quit.\n")

    try:
        while not shutdown.is_set():
            try:
                event = events.get(timeout=0.5)
            except queue.Empty:
                continue

            if event == "quit":
                shutdown.set()
                break

            if event != "toggle":
                continue

            current = state.state

            if current is State.IDLE:
                if not state.try_transition(State.RECORDING):
                    continue  # Debounce — too soon after last transition.
                print("[state] IDLE -> RECORDING")
                try:
                    recorder.start()
                except Exception as e:  # noqa: BLE001
                    print(f"[error] could not start recording: {e}")
                    state.try_transition(State.IDLE)
                continue

            if current is State.RECORDING:
                if not state.try_transition(State.TRANSCRIBING):
                    continue
                print("[state] RECORDING -> TRANSCRIBING")

                audio = recorder.stop()
                if audio is None or len(audio) < MIN_AUDIO_SECONDS * SAMPLE_RATE:
                    print(f"[skip] audio too short (< {MIN_AUDIO_SECONDS}s)")
                    # Bypass debounce on the way back to IDLE so the next press works.
                    while not state.try_transition(State.IDLE):
                        threading.Event().wait(0.05)
                    continue

                try:
                    text = engine.transcribe(audio)
                except TranscriptionError as e:
                    print(f"[error] {e}")
                    while not state.try_transition(State.IDLE):
                        threading.Event().wait(0.05)
                    continue

                if not text:
                    print("[skip] empty transcript")
                    while not state.try_transition(State.IDLE):
                        threading.Event().wait(0.05)
                    continue

                print(f"[transcript] {text!r}")

                while not state.try_transition(State.PASTING):
                    threading.Event().wait(0.05)
                print("[state] TRANSCRIBING -> PASTING")
                try:
                    paste_text(text)
                except Exception as e:  # noqa: BLE001
                    print(f"[error] paste failed: {e}")

                while not state.try_transition(State.IDLE):
                    threading.Event().wait(0.05)
                print("[state] PASTING -> IDLE\n")
                continue

            # current is TRANSCRIBING or PASTING — re-trigger is a no-op.
            print(f"[ignore] hotkey during {current.value}")

    finally:
        listener.stop()
        if recorder.is_recording():
            recorder.stop()
        print("Slumbr stopped.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
