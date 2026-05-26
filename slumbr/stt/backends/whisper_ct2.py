"""faster-whisper / CTranslate2 backend — NVIDIA's max-perf path.

Thin delegation around the original ``WhisperEngine``. We keep
``WhisperEngine`` as-is in Phase 1 so the proven CUDA + OOM-retry path
isn't disturbed by the protocol rearch. Once every call site routes
through the factory we can collapse ``engine.py`` into this file.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

from ..engine import WhisperEngine

if TYPE_CHECKING:
    from ...config import BackendConfig

log = logging.getLogger(__name__)


class WhisperCT2Transcriber:
    """Adapter exposing ``Transcriber`` over the existing WhisperEngine.

    Serves two backend names off the same engine:
      - ``cuda_ct2`` → faster-whisper on the NVIDIA GPU (max perf).
      - ``cpu_ct2``  → faster-whisper on the CPU (int8). This is the
        *accuracy* path for no-GPU machines: real Whisper quality
        (small/medium/base) where Moonshine base would otherwise be the
        ceiling. Slower than Moonshine, so it's offered as an opt-in tier,
        not the seamless default. Needs no extra dependency — faster-
        whisper/CTranslate2 already ship and run on CPU.
    """

    backend_name = "cuda_ct2"

    def __init__(self, cfg: BackendConfig, *, language: str | None, initial_prompt: str) -> None:
        # Device: cpu_ct2 forces CPU; cuda_ct2 defaults to CUDA but honors
        # an explicit cfg.extra["device"] override (diagnostic runs).
        if cfg.name == "cpu_ct2":
            device = "cpu"
        else:
            device = cfg.extra.get("device", "cuda") if cfg.extra else "cuda"
        # float16 is a GPU format; CPU wants plain int8.
        default_compute = "int8" if device == "cpu" else "int8_float16"
        self.backend_name = cfg.name  # report the actual backend (cuda_ct2 / cpu_ct2)
        self._engine = WhisperEngine(
            model_size=cfg.model,
            device=device,
            compute_type=cfg.compute_type or default_compute,
            language=language or None,
            initial_prompt=initial_prompt,
        )

    def warm_up(self) -> None:
        self._engine.warm_up()

    def transcribe(self, audio: np.ndarray) -> str:
        return self._engine.transcribe(audio)

    def set_runtime_config(
        self,
        *,
        language: str | None = None,
        initial_prompt: str | None = None,
    ) -> None:
        self._engine.set_runtime_config(
            language=language,
            initial_prompt=initial_prompt or "",
        )

    def close(self) -> None:
        # faster-whisper / CTranslate2 has no explicit close — the
        # model releases when the WhisperModel goes out of scope.
        # Drop the reference so GC reclaims VRAM promptly when a
        # mid-session backend swap is happening.
        self._engine = None  # type: ignore[assignment]
