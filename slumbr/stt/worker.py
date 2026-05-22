"""QThread wrapper around the Whisper engine.

Lives in its own module so `app.py` doesn't have to host a QThread class
alongside the SlumbrApp orchestrator. The worker runs `transcribe()` off
the Qt main thread; `done` and `failed` auto-queue back to the main
thread via Qt's signal/slot machinery.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QThread, Signal

from .engine import TranscriptionError, WhisperEngine


class TranscribeWorker(QThread):
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, engine: WhisperEngine, audio: np.ndarray) -> None:
        super().__init__()
        self._engine = engine
        self._audio = audio

    def run(self) -> None:
        try:
            text = self._engine.transcribe(self._audio)
            self.done.emit(text)
        except TranscriptionError as e:
            self.failed.emit(str(e))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"unexpected: {e!r}")
