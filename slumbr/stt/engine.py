from __future__ import annotations

import time

import numpy as np
from faster_whisper import WhisperModel


class TranscriptionError(RuntimeError):
    pass


class WhisperEngine:
    """faster-whisper wrapper with startup warm-up and OOM-aware retry.

    The warm-up call on a 0.5 s silence buffer is what takes first-real-utterance
    latency from ~3 s down to ~400 ms — don't skip it.
    """

    def __init__(
        self,
        model_size: str = "large-v3-turbo",
        device: str = "cuda",
        compute_type: str = "int8",
        language: str | None = None,
    ) -> None:
        print(f"[engine] loading {model_size!r} on {device} ({compute_type})...")
        t0 = time.monotonic()
        self.model = WhisperModel(model_size, device=device, compute_type=compute_type)
        self.language = language
        self.model_size = model_size
        print(f"[engine] model loaded in {time.monotonic() - t0:.1f}s")

    def warm_up(self) -> None:
        print("[engine] warming up...")
        t0 = time.monotonic()
        silence = np.zeros(int(0.5 * 16000), dtype=np.float32)
        self._run(silence, beam_size=1)
        print(f"[engine] warm-up done in {time.monotonic() - t0:.2f}s")

    def _run(self, audio: np.ndarray, beam_size: int = 5) -> str:
        segments, _info = self.model.transcribe(
            audio,
            language=self.language,
            vad_filter=False,
            beam_size=beam_size,
        )
        return " ".join(seg.text.strip() for seg in segments).strip()

    def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe with one OOM-aware retry at beam_size=1."""
        t0 = time.monotonic()
        try:
            text = self._run(audio, beam_size=5)
        except RuntimeError as e:
            msg = str(e).lower()
            if "out of memory" in msg or "cublas" in msg or "cudnn" in msg:
                print(f"[engine] CUDA error, retrying at beam_size=1: {e}")
                try:
                    text = self._run(audio, beam_size=1)
                except Exception as e2:  # noqa: BLE001
                    raise TranscriptionError(f"transcription failed: {e2}") from e2
            else:
                raise TranscriptionError(f"transcription failed: {e}") from e
        dur = time.monotonic() - t0
        audio_s = len(audio) / 16000
        print(f"[engine] transcribed {audio_s:.1f}s of audio in {dur:.2f}s")
        return text
