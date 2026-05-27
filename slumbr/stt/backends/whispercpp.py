"""whisper.cpp backend — via ``pywhispercpp`` 1.4.1+.

Two variants share one class:

  - ``whispercpp_cpu`` — universal Windows CPU path. Uses the pre-built
    pywhispercpp wheel from PyPI. Lighter than DirectML for users who
    don't have a usable GPU and want Whisper-quality output (at the
    cost of ~3–6 s latency on a 5 s utterance with quantized small).

  - ``whispercpp_sycl`` — Intel Arc + iGPU GPU path. Requires a
    whisper.cpp DLL built with ``-DGGML_SYCL=ON`` against Intel oneAPI.
    pywhispercpp doesn't ship that wheel; users would need to compile
    or grab a third-party prebuild. **Slumbr Phase 2 raises a clear
    error here.** A future Phase 2C will ship a bundled SYCL DLL.
    Intel users in Phase 2 are routed to DirectML instead.

Model identifiers follow pywhispercpp / ggerganov conventions:
  - ``tiny`` / ``tiny.en``
  - ``base`` / ``base.en``
  - ``small`` / ``small.en``
  - ``medium`` / ``medium.en``
  - ``large-v3``
  - Quantized suffixes: ``-q5_k_m``, ``-q8_0``, ``-q4_k_m`` etc.

Slumbr's default for whispercpp_cpu is ``small.en-q5_k_m``: the
research-locked sweet spot for CPU dictation (~3 s latency, ~12 % WER).
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

import numpy as np

from ..protocol import TranscriptionError

if TYPE_CHECKING:
    from ...config import BackendConfig

log = logging.getLogger(__name__)


# Models that pywhispercpp can auto-download (from ggerganov's HF repo).
# We accept Slumbr's internal model names and translate to pywhispercpp's
# expected forms — primarily mapping bare names to quantized English-only
# variants where appropriate.
def _resolve_model_id(slumbr_name: str) -> str:
    """Map a Slumbr model string to the pywhispercpp model identifier.

    Returns ``slumbr_name`` unchanged if it already looks like a
    pywhispercpp id (contains a quant suffix or ``.en``).
    """
    name = slumbr_name.strip()
    if not name:
        return "small.en-q5_k_m"
    # Already in pywhispercpp form (".en" or quantization suffix)
    if ".en" in name or "-q" in name:
        return name
    # Bare size names → English-only quantized
    if name in {"tiny", "base", "small", "medium"}:
        return f"{name}.en-q5_k_m"
    return name


class WhisperCppTranscriber:
    """``whispercpp_cpu`` is functional; ``whispercpp_sycl`` raises
    a clear error until Phase 2C ships the SYCL binary.
    """

    def __init__(self, cfg: BackendConfig, *, language: str | None, initial_prompt: str) -> None:
        self._backend_name = cfg.name

        if cfg.name == "whispercpp_sycl":
            raise NotImplementedError(
                "Intel SYCL backend (whispercpp_sycl) needs a custom whisper.cpp "
                "DLL built with -DGGML_SYCL=ON against Intel oneAPI — Slumbr will "
                "bundle that in Phase 2C. For now, switch to DirectML in the "
                "Engine tab (same Intel GPU, slightly slower) or to "
                "whispercpp_cpu (no GPU acceleration but works everywhere)."
            )

        try:
            from pywhispercpp.model import Model  # noqa: PLC0415
        except ImportError as e:
            raise ImportError(
                "whispercpp_cpu backend needs the `cpu` extras. Run "
                "`pip install slumbr[cpu]` (or relaunch and let the wizard install)."
            ) from e

        model_id = _resolve_model_id(cfg.model)
        threads = cfg.threads or 4
        log.info("loading whisper.cpp model=%r threads=%d", model_id, threads)
        t0 = time.monotonic()
        try:
            self._model = Model(
                model_id,
                n_threads=threads,
                # Tame the verbose default output — Slumbr already has
                # its own logging story; we don't want whisper.cpp
                # printing partials to stdout during dictation.
                print_realtime=False,
                print_progress=False,
                print_timestamps=False,
                print_special=False,
            )
        except Exception as e:  # noqa: BLE001
            raise TranscriptionError(f"could not load whisper.cpp model {model_id!r}: {e}") from e

        self._language = language or "en"
        self._initial_prompt = initial_prompt
        self._model_id = model_id
        log.info("whisper.cpp loaded in %.1fs", time.monotonic() - t0)

    @property
    def backend_name(self) -> str:
        return self._backend_name

    def warm_up(self) -> None:
        log.info("warming up whisper.cpp...")
        t0 = time.monotonic()
        silence = np.zeros(int(0.5 * 16000), dtype=np.float32)
        try:
            self._decode(silence)
        except Exception as e:  # noqa: BLE001
            log.warning("whisper.cpp warm-up raised (non-fatal): %s", e)
        log.info("whisper.cpp warm-up done in %.2fs", time.monotonic() - t0)

    def transcribe(self, audio: np.ndarray) -> str:
        t0 = time.monotonic()
        try:
            text = self._decode(audio)
        except TranscriptionError:
            raise
        except Exception as e:  # noqa: BLE001
            raise TranscriptionError(f"whisper.cpp transcribe failed: {e}") from e
        dur = time.monotonic() - t0
        audio_s = len(audio) / 16000
        log.info("whisper.cpp transcribed %.1fs of audio in %.2fs", audio_s, dur)
        return text

    def set_runtime_config(
        self,
        *,
        language: str | None = None,
        initial_prompt: str | None = None,
    ) -> None:
        if language is not None:
            self._language = language or "en"
        if initial_prompt is not None:
            self._initial_prompt = initial_prompt

    def close(self) -> None:
        self._model = None  # type: ignore[assignment]

    # ----------------------------------------------------- internals

    def _decode(self, audio: np.ndarray) -> str:
        # pywhispercpp wants float32, 16 kHz, mono — same as everywhere
        # else in slumbr. `transcribe` accepts a numpy array directly.
        segments = self._model.transcribe(
            audio.astype(np.float32, copy=False),
            language=self._language,
            initial_prompt=self._initial_prompt or "",
        )
        if not segments:
            return ""
        return " ".join(seg.text.strip() for seg in segments).strip()
