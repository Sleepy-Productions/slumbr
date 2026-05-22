"""Streaming Zipformer (sherpa-onnx) — append-only leading edge for the popup.

Optional sibling of the Moonshine path in `streaming_engine.py`. When the
user enables `streaming_visual_leading_edge` in config, Slumbr runs this
recognizer in parallel and uses its append-only token stream to feed the
uncommitted tail of the popup display. Moonshine + LocalAgreement-2 still
owns the committed prefix — Zipformer just makes the leading edge feel
snappier (~50 ms per token vs Moonshine's ~200 ms re-decode cadence).

Why this is experimental
------------------------
The only English streaming Zipformer that ships with sherpa-onnx today
(2023-06-21 release) is trained on LibriSpeech + GigaSpeech — clean read
speech. Its WER on real conversational dictation is materially worse
than Moonshine's. So its output is **never** used as the source of
truth, only as a visual cue for what the system *thinks* you might be
about to say. If LA-2 disagrees, Moonshine wins.

Model files downloaded to %APPDATA%\\Slumbr\\models\\streaming-zipformer-en
on first use (~180 MB int8). Subsequent launches reuse the cache.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

import numpy as np
import sherpa_onnx

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000

_MODELS_ROOT = Path(os.path.expandvars(r"%APPDATA%\Slumbr\models"))
_ZIPFORMER_DIR = _MODELS_ROOT / "streaming-zipformer-en"
_ZIPFORMER_HF = "csukuangfj/sherpa-onnx-streaming-zipformer-en-2023-06-21"
_ZIPFORMER_FILES = [
    "encoder-epoch-99-avg-1.int8.onnx",
    "decoder-epoch-99-avg-1.int8.onnx",
    "joiner-epoch-99-avg-1.int8.onnx",
    "tokens.txt",
]


class ModelDownloadError(RuntimeError):
    pass


def _ensure_zipformer() -> dict[str, str]:
    if all((_ZIPFORMER_DIR / f).is_file() for f in _ZIPFORMER_FILES):
        return {f: str(_ZIPFORMER_DIR / f) for f in _ZIPFORMER_FILES}

    log.info("downloading streaming Zipformer en int8 (~180 MB) to %s", _ZIPFORMER_DIR)
    _ZIPFORMER_DIR.mkdir(parents=True, exist_ok=True)
    from huggingface_hub import snapshot_download

    snapshot_download(
        repo_id=_ZIPFORMER_HF,
        local_dir=str(_ZIPFORMER_DIR),
        allow_patterns=_ZIPFORMER_FILES,
    )
    missing = [f for f in _ZIPFORMER_FILES if not (_ZIPFORMER_DIR / f).is_file()]
    if missing:
        raise ModelDownloadError(f"streaming Zipformer files missing after download: {missing}")
    return {f: str(_ZIPFORMER_DIR / f) for f in _ZIPFORMER_FILES}


def _build_recognizer(num_threads: int) -> sherpa_onnx.OnlineRecognizer:
    files = _ensure_zipformer()
    return sherpa_onnx.OnlineRecognizer.from_transducer(
        encoder=files["encoder-epoch-99-avg-1.int8.onnx"],
        decoder=files["decoder-epoch-99-avg-1.int8.onnx"],
        joiner=files["joiner-epoch-99-avg-1.int8.onnx"],
        tokens=files["tokens.txt"],
        num_threads=num_threads,
        sample_rate=SAMPLE_RATE,
        feature_dim=80,
        decoding_method="greedy_search",
        provider="cpu",
    )


class StreamingZipformer:
    """Append-only leading-edge recognizer.

    Lifecycle mirrors `StreamingASREngine` so the engine can drive both
    side-by-side:

        zf.start_session()
        zf.feed(chunk)        # returns the current cumulative text
        zf.end_session()      # drops the stream; safe to start_session again
    """

    def __init__(self, num_threads: int = 2) -> None:
        log.info("loading streaming Zipformer en int8...")
        self._recognizer = _build_recognizer(num_threads)
        self._stream: sherpa_onnx.OnlineStream | None = None
        self._lock = threading.Lock()
        log.info("streaming Zipformer ready")

    def start_session(self) -> None:
        with self._lock:
            self._stream = self._recognizer.create_stream()

    def feed(self, chunk: np.ndarray) -> str:
        """Push audio in. Returns the current cumulative transcript."""
        if self._stream is None:
            return ""
        if chunk.ndim > 1:
            chunk = chunk.reshape(-1)
        chunk = chunk.astype(np.float32, copy=False)
        with self._lock:
            stream = self._stream
            if stream is None:
                return ""
            stream.accept_waveform(SAMPLE_RATE, chunk)
            while self._recognizer.is_ready(stream):
                self._recognizer.decode_stream(stream)
            result = self._recognizer.get_result(stream)
        # Across sherpa-onnx versions `get_result` returns either a str
        # or a result object with `.text`. Handle both without crashing.
        if result is None:
            return ""
        text = result if isinstance(result, str) else getattr(result, "text", "")
        return (text or "").strip()

    def end_session(self) -> None:
        with self._lock:
            self._stream = None
