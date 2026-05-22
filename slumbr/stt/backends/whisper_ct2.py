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
    """Adapter exposing ``Transcriber`` over the existing WhisperEngine."""

    backend_name = "cuda_ct2"

    def __init__(self, cfg: BackendConfig, *, language: str | None, initial_prompt: str) -> None:
        # faster-whisper accepts ``device="cuda"`` or ``"cpu"``; the
        # CT2 path is intended for CUDA. CPU fallback exists but is
        # slow — Moonshine is the better CPU pick. We let the user
        # opt into ``device="cpu"`` via cfg.extra for diagnostic runs
        # but don't recommend it in the wizard.
        device = cfg.extra.get("device", "cuda") if cfg.extra else "cuda"
        self._engine = WhisperEngine(
            model_size=cfg.model,
            device=device,
            compute_type=cfg.compute_type or "int8_float16",
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
