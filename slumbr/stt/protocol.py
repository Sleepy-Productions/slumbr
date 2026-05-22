"""The Transcriber protocol — every STT backend implements this.

Keep the surface tiny. `app.py` only calls four methods on the engine
(`warm_up`, `transcribe`, `set_runtime_config`, plus `close` at quit),
so that's all the protocol exposes. Per-backend construction takes a
typed `BackendConfig` slice via the factory, not through this Protocol —
that's why `__init__` is deliberately absent.

If you add a method here, every backend has to grow it. Resist.

``TranscriptionError`` also lives here (rather than in ``engine.py``)
so the worker module can import it without dragging in faster_whisper
+ ctranslate2. AMD / Intel / CPU users don't have those wheels and
shouldn't need them to import slumbr.stt.worker.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np


class TranscriptionError(RuntimeError):
    """Raised by any backend's ``transcribe()`` when decoding fails.

    Backends should wrap their native exceptions in this so the
    worker's ``failed`` signal can show a unified error to the user.
    """


@runtime_checkable
class Transcriber(Protocol):
    @property
    def backend_name(self) -> str:
        """Stable id for telemetry / Settings display (e.g. 'cuda_ct2')."""
        ...

    def warm_up(self) -> None:
        """Eat the first-utterance latency cost up-front.

        Backends that don't have a meaningful warm-up (e.g. tiny ONNX
        runtimes) may make this a no-op, but should still implement it
        so app.py can call it unconditionally.
        """
        ...

    def transcribe(self, audio: np.ndarray) -> str:
        """Synchronous decode. Called off the Qt main thread via
        TranscribeWorker. Returns plain text — caller does cosmetic polish.
        """
        ...

    def set_runtime_config(
        self,
        *,
        language: str | None = None,
        initial_prompt: str | None = None,
    ) -> None:
        """Hot-tunable knobs. Both fields may be ignored by backends that
        don't support them (Moonshine ignores `language` because it's
        English-only; whisper.cpp ignores `initial_prompt` in some
        configurations).
        """
        ...

    def close(self) -> None:
        """Release model handles / GPU memory. Called at app quit and
        on mid-session backend swap from the Settings dialog.
        """
        ...
