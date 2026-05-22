"""Moonshine — the primary engine for CPU-only users.

Why this is here and not just "Moonshine partials on CPU":
- Whisper on CPU misses Slumbr's <500 ms dictation latency target by
  10-20× (~4-10 s for a 5 s utterance). Moonshine Small decodes a 5 s
  utterance in ~150-300 ms on a 2024+ desktop CPU and actually beats
  Whisper Small on real-world WER (7.84 % vs 8.59 %).
- Moonshine's raw output is lowercase, no punctuation. Slumbr's
  ``StreamingASREngine`` already ships an ``online-punct-en`` ONNX
  model for that — we reuse the same downloader / model files here
  rather than re-implementing the post-processing.

In Phase 1 we deliberately load Moonshine *twice* if the user picks it
as the primary engine: once here, once inside ``StreamingASREngine``
for popup partials. ~200 MB of RAM duplication. Acceptable on the CPU-
only tier; will be deduplicated via a shared model registry in Phase 3.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

import numpy as np

# Borrow the model download helpers from the streaming engine so the
# wizard's "first launch downloads models" UX stays in one place.
from ..streaming_engine import _ensure_moonshine, _ensure_punct

if TYPE_CHECKING:
    from ...config import BackendConfig

log = logging.getLogger(__name__)


class MoonshineTranscriber:
    """Offline Moonshine decode + online-punct post-processing."""

    backend_name = "moonshine"

    def __init__(self, cfg: BackendConfig, *, language: str | None, initial_prompt: str) -> None:
        # Moonshine is English-only. The factory enforces this — we
        # don't silently ignore a non-English ``language`` here.
        if language and language != "en":
            raise ValueError(
                f"Moonshine is English-only; got language={language!r}. "
                "Switch to NVIDIA/AMD/Intel backend or pick English."
            )

        # Import inside __init__ so the sherpa-onnx wheel only loads
        # when this backend is actually constructed.
        import sherpa_onnx  # noqa: PLC0415

        threads = cfg.threads if cfg.threads and cfg.threads > 0 else 4
        log.info("loading Moonshine offline recognizer (%d threads)...", threads)
        t0 = time.monotonic()
        files = _ensure_moonshine()
        self._recognizer = sherpa_onnx.OfflineRecognizer.from_moonshine(
            preprocessor=files["preprocess.onnx"],
            encoder=files["encode.int8.onnx"],
            uncached_decoder=files["uncached_decode.int8.onnx"],
            cached_decoder=files["cached_decode.int8.onnx"],
            tokens=files["tokens.txt"],
            num_threads=threads,
            provider="cpu",
        )
        log.info("Moonshine loaded in %.2fs", time.monotonic() - t0)

        # Punctuator is best-effort. If the download fails (rare) we
        # still ship output — just lowercase + no commas. Worse but
        # not broken.
        self._punct = None
        punct_paths = _ensure_punct()
        if punct_paths:
            try:
                model_path, vocab_path = punct_paths
                cfg_obj = sherpa_onnx.OnlinePunctuationConfig(
                    model=sherpa_onnx.OnlinePunctuationModelConfig(
                        cnn_bilstm=model_path,
                        bpe_vocab=vocab_path,
                    )
                )
                self._punct = sherpa_onnx.OnlinePunctuation(cfg_obj)
                log.info("Moonshine punctuator ready")
            except Exception as e:  # noqa: BLE001
                log.warning("punctuator init failed; output will be lowercase: %s", e)

        # initial_prompt is recognised by Whisper but not by Moonshine;
        # we keep the field for parity so set_runtime_config doesn't
        # blow up when called.
        self._initial_prompt = initial_prompt
        self._lock = threading.Lock()

    def warm_up(self) -> None:
        log.info("warming up Moonshine...")
        t0 = time.monotonic()
        silence = np.zeros(int(0.5 * 16000), dtype=np.float32)
        self._decode(silence)
        log.info("Moonshine warm-up done in %.2fs", time.monotonic() - t0)

    def transcribe(self, audio: np.ndarray) -> str:
        t0 = time.monotonic()
        text = self._decode(audio)
        polished = self._punctuate(text)
        dur = time.monotonic() - t0
        audio_s = len(audio) / 16000
        log.info(
            "Moonshine transcribed %.1fs of audio in %.2fs", audio_s, dur
        )
        return polished

    def set_runtime_config(
        self,
        *,
        language: str | None = None,
        initial_prompt: str | None = None,
    ) -> None:
        if language and language != "en":
            # Don't crash here — config UI might briefly hold an
            # invalid state mid-edit. The factory rejects the
            # combination at construction time. Just no-op the
            # language field here.
            log.warning("Moonshine ignoring non-English language=%r", language)
        if initial_prompt is not None:
            self._initial_prompt = initial_prompt

    def close(self) -> None:
        self._recognizer = None  # type: ignore[assignment]
        self._punct = None

    # ----------------------------------------------------- internals

    def _decode(self, audio: np.ndarray) -> str:
        """One-shot Moonshine decode under the shared lock. ``audio``
        must be 16 kHz float32 in [-1, 1] — same contract as Whisper.
        """
        # ``sherpa-onnx`` is not documented as thread-safe for
        # concurrent decode calls. The streaming engine takes a lock
        # around its own recognizer; we follow the same pattern even
        # though our recognizer instance is independent.
        with self._lock:
            stream = self._recognizer.create_stream()
            stream.accept_waveform(16000, audio.astype(np.float32, copy=False))
            self._recognizer.decode_stream(stream)
            return (stream.result.text or "").strip()

    def _punctuate(self, raw: str) -> str:
        """Run the online-punct model over Moonshine output. The model
        adds commas / periods / question marks and truecases.
        """
        if not raw:
            return ""
        if self._punct is None:
            return raw
        try:
            return self._punct.add_punctuation_with_case(raw)
        except Exception as e:  # noqa: BLE001
            log.debug("punctuation failed for %r: %s", raw, e)
            return raw
