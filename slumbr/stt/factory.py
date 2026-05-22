"""Dispatch a ``BackendConfig`` to the right concrete Transcriber.

Only call site: ``slumbr/app.py`` (and the Settings dialog when the
user picks a different backend mid-session). Add new backends here +
in ``slumbr/stt/backends/`` and they become reachable everywhere.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .protocol import Transcriber

if TYPE_CHECKING:
    from ..config import BackendConfig

log = logging.getLogger(__name__)


def build_transcriber(
    cfg: BackendConfig,
    *,
    language: str | None,
    initial_prompt: str,
) -> Transcriber:
    """Instantiate the backend named in ``cfg``.

    ``language`` and ``initial_prompt`` are the only "hot" knobs we
    push through ``set_runtime_config()`` later — pass them here too
    so the engine warms up with the user's actual config.
    """
    name = cfg.name
    log.info("building transcriber: backend=%s model=%s", name, cfg.model)

    if name == "cuda_ct2":
        from .backends.whisper_ct2 import WhisperCT2Transcriber  # noqa: PLC0415
        return WhisperCT2Transcriber(cfg, language=language, initial_prompt=initial_prompt)

    if name == "moonshine":
        from .backends.moonshine import MoonshineTranscriber  # noqa: PLC0415
        return MoonshineTranscriber(cfg, language=language, initial_prompt=initial_prompt)

    if name == "directml":
        from .backends.directml import DirectMLTranscriber  # noqa: PLC0415
        return DirectMLTranscriber(cfg, language=language, initial_prompt=initial_prompt)

    if name in ("whispercpp_sycl", "whispercpp_cpu"):
        from .backends.whispercpp import WhisperCppTranscriber  # noqa: PLC0415
        return WhisperCppTranscriber(cfg, language=language, initial_prompt=initial_prompt)

    raise ValueError(f"unknown backend name: {name!r}")
