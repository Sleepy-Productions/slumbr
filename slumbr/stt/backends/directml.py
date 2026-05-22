"""DirectML backend — Whisper via ONNX Runtime DirectML.

Universal Windows GPU path. Works on:
  - AMD Radeon RX (any DX12 GPU, ~8–12× RTF on medium models)
  - Intel Arc + Xe iGPUs
  - NVIDIA cards (worse than cuda_ct2 — we don't recommend it on NVIDIA)

Implementation choice: `optimum.onnxruntime.ORTModelForSpeechSeq2Seq`
+ `transformers.WhisperProcessor`. This is the maintained Whisper-on-ONNX
path; the alternative (raw onnxruntime + handwritten mel-spectrogram +
custom decode loop) is ~300 LOC of fragile code we don't want to own.

Tradeoff: the optimum/transformers stack is heavy (~500 MB on top of
the base venv) because it transitively pulls torch. Phase 3 may revisit
this for a leaner stack.

First-load model conversion:
  ``ORTModelForSpeechSeq2Seq.from_pretrained(model_id, export=True)``
  downloads the PyTorch checkpoint, exports it to ONNX, and caches the
  result. First load takes 1–3 minutes for `whisper-small`. Subsequent
  loads are ~3–5 seconds.

Models cached at: ``~/.cache/huggingface/hub`` (HF's standard location).
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


# Map Slumbr's internal model names → Hugging Face repo IDs.
# `.en` variants are English-only and noticeably more accurate on
# English speech than the multilingual base. Slumbr defaults to `.en`
# because dictation is overwhelmingly English in practice.
_HF_MODEL_MAP: dict[str, str] = {
    "tiny": "openai/whisper-tiny.en",
    "base": "openai/whisper-base.en",
    "small": "openai/whisper-small.en",
    "medium": "openai/whisper-medium.en",
    "large-v3": "openai/whisper-large-v3",
    "large-v3-turbo": "openai/whisper-large-v3-turbo",
}


class DirectMLTranscriber:
    """Whisper via ONNX Runtime DirectML."""

    backend_name = "directml"

    def __init__(self, cfg: BackendConfig, *, language: str | None, initial_prompt: str) -> None:
        # Lazy imports — heavy stuff stays out of the import graph for
        # users who didn't pick this backend.
        try:
            from optimum.onnxruntime import ORTModelForSpeechSeq2Seq  # noqa: PLC0415
            from transformers import WhisperProcessor  # noqa: PLC0415
        except ImportError as e:
            raise ImportError(
                "DirectML backend needs the `amd` extras. Run "
                "`pip install slumbr[amd]` (or relaunch and let the wizard install)."
            ) from e

        hf_id = _HF_MODEL_MAP.get(cfg.model, f"openai/whisper-{cfg.model}")
        log.info("loading %s on DirectML (first load downloads + exports to ONNX)...", hf_id)
        t0 = time.monotonic()

        # Optimum will reuse a previously-exported ONNX from the HF cache;
        # ``export=True`` only triggers the export if the cache is empty.
        try:
            self._model = ORTModelForSpeechSeq2Seq.from_pretrained(
                hf_id,
                export=True,
                provider="DmlExecutionProvider",
            )
        except Exception as e:  # noqa: BLE001
            raise TranscriptionError(
                f"could not load Whisper {cfg.model!r} on DirectML: {e}"
            ) from e

        self._processor = WhisperProcessor.from_pretrained(hf_id)
        self._language = language or "en"
        self._initial_prompt = initial_prompt
        self._model_id = hf_id
        log.info("DirectML model loaded in %.1fs", time.monotonic() - t0)

    def warm_up(self) -> None:
        log.info("warming up DirectML decoder...")
        t0 = time.monotonic()
        silence = np.zeros(int(0.5 * 16000), dtype=np.float32)
        self._decode(silence)
        log.info("DirectML warm-up done in %.2fs", time.monotonic() - t0)

    def transcribe(self, audio: np.ndarray) -> str:
        t0 = time.monotonic()
        try:
            text = self._decode(audio)
        except TranscriptionError:
            raise
        except Exception as e:  # noqa: BLE001
            raise TranscriptionError(f"DirectML transcribe failed: {e}") from e
        dur = time.monotonic() - t0
        audio_s = len(audio) / 16000
        log.info(
            "DirectML transcribed %.1fs of audio in %.2fs", audio_s, dur
        )
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
        # Release ONNX session — drops VRAM allocations on DirectML.
        self._model = None  # type: ignore[assignment]
        self._processor = None  # type: ignore[assignment]

    # ----------------------------------------------------- internals

    def _decode(self, audio: np.ndarray) -> str:
        """Whisper end-to-end: mel spectrogram → encoder → decoder loop.

        ``WhisperProcessor`` handles the feature-extraction front-end.
        ``ORTModelForSpeechSeq2Seq.generate`` runs the autoregressive
        decoder under the hood. We ask for English transcription
        explicitly via ``language`` + ``task=transcribe`` so the model
        skips its own language-ID step.
        """
        inputs = self._processor(
            audio.astype(np.float32, copy=False),
            sampling_rate=16000,
            return_tensors="pt",
        )

        # ``forced_decoder_ids`` pins the language + task tokens at the
        # start of the decoder sequence. With them set we avoid Whisper's
        # auto language detection (which can mis-route short utterances
        # to Welsh / Dutch and decode garbage).
        try:
            forced_decoder_ids = self._processor.get_decoder_prompt_ids(
                language=self._language,
                task="transcribe",
            )
        except (ValueError, KeyError):
            # `.en`-only models can't accept language kwargs — they're
            # already English-only. Skip the override.
            forced_decoder_ids = None

        gen_kwargs: dict[str, object] = {}
        if forced_decoder_ids is not None:
            gen_kwargs["forced_decoder_ids"] = forced_decoder_ids

        # initial_prompt support: Whisper accepts a `prompt_ids` tensor
        # that becomes prefix context for decoding. Skip if empty.
        if self._initial_prompt.strip():
            try:
                prompt_ids = self._processor.get_prompt_ids(
                    self._initial_prompt,
                    return_tensors="pt",
                )
                gen_kwargs["prompt_ids"] = prompt_ids
            except Exception as e:  # noqa: BLE001
                log.debug("prompt_ids encoding failed; skipping: %s", e)

        ids = self._model.generate(inputs.input_features, **gen_kwargs)
        text = self._processor.batch_decode(ids, skip_special_tokens=True)[0]
        return text.strip()
