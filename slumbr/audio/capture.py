"""Mono 16 kHz audio capture with always-on stream and pre-buffer.

Design:
- The PortAudio `InputStream` is opened on construction and stays open
  for the lifetime of the recorder. This avoids the ~200 ms WASAPI
  warm-up that was eating the first words of every utterance — by the
  time the user taps Caps Lock, the stream has already been capturing
  silence into a ring buffer.
- A 500 ms ring buffer keeps the most-recent audio. When `start()` is
  called we seed the recording chunks from that buffer, so words the
  user said *before* tapping the hotkey are still in the recording.
- `on_chunk` only fires while `_saving` is True, so the visualizer
  doesn't paint on idle audio.

Side effect: the Windows microphone indicator stays lit while Slumbr is
running, even when idle. We accept that as the price of zero-cutoff.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "float32"
BLOCKSIZE = 1024
PREBUFFER_SECONDS = 0.5


class AudioRecorder:
    def __init__(
        self,
        samplerate: int = SAMPLE_RATE,
        channels: int = CHANNELS,
        device: int | str | None = None,
        on_chunk: Callable[[np.ndarray], None] | None = None,
        prebuffer_seconds: float = PREBUFFER_SECONDS,
    ) -> None:
        self.samplerate = samplerate
        self.channels = channels
        self.device = device
        self.on_chunk = on_chunk
        # +1 because a partially-filled block counts.
        prebuffer_blocks = int(prebuffer_seconds * samplerate / BLOCKSIZE) + 1
        self._prebuffer: deque[np.ndarray] = deque(maxlen=prebuffer_blocks)
        self._chunks: list[np.ndarray] = []
        self._saving = False
        self._lock = threading.Lock()
        self._stream: sd.InputStream | None = None
        self._open_stream()

    # -------------------------------------------------------- lifecycle
    def _open_stream(self) -> None:
        if self._stream is not None:
            return
        # Resolve string device names to an explicit int index so we
        # dodge sounddevice's "Multiple input devices found" error
        # when the same name lives under MME + DirectSound + WASAPI
        # (e.g. "Microphone (HyperX QuadCast 2 S)" after VB-Cable is
        # installed and Windows enumerates the new endpoints). The
        # resolver prefers WASAPI and tolerates MME's 31-char name
        # truncation. ``None`` (= system default) is passed through.
        resolved: int | str | None = self.device
        if isinstance(self.device, str):
            from .mirror import resolve_device_index  # noqa: PLC0415
            idx = resolve_device_index(self.device, want_input=True)
            if idx is not None:
                resolved = idx
            else:
                log.warning(
                    "could not resolve input device %r — falling back to default",
                    self.device,
                )
                resolved = None
        try:
            self._stream = sd.InputStream(
                samplerate=self.samplerate,
                channels=self.channels,
                dtype=DTYPE,
                blocksize=BLOCKSIZE,
                device=resolved,
                callback=self._callback,
            )
            self._stream.start()
            log.info("stream open (device=%r -> %r)", self.device, resolved)
        except Exception as e:  # noqa: BLE001
            log.error("could not open stream: %s", e)
            self._stream = None

    def close(self) -> None:
        if self._stream is None:
            return
        try:
            self._stream.stop()
            self._stream.close()
        except Exception as e:  # noqa: BLE001
            log.warning("close failed: %s", e)
        self._stream = None

    def set_device(self, device: int | str | None) -> None:
        """Switch input device — reopens the stream."""
        if device == self.device:
            return
        self.device = device
        self.close()
        with self._lock:
            self._prebuffer.clear()
            self._chunks = []
            self._saving = False
        self._open_stream()

    # ----------------------------------------------------- callback path
    def _callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        if status:
            log.warning("xrun/status: %s", status)
        chunk = indata.copy()
        with self._lock:
            self._prebuffer.append(chunk)
            if self._saving:
                self._chunks.append(chunk)
                saving = True
            else:
                saving = False
        # Only feed the visualizer while we're actually recording — keeps
        # the popup quiet in idle state.
        if saving and self.on_chunk is not None:
            try:
                self.on_chunk(chunk)
            except Exception as e:  # noqa: BLE001
                log.error("on_chunk raised: %s", e)

    # -------------------------------------------------------- recording
    def start(self) -> None:
        """Begin saving captured audio. Pre-buffer is seeded into the recording."""
        with self._lock:
            self._chunks = list(self._prebuffer)  # seed with prior ~500 ms
            self._saving = True

    def stop(self) -> np.ndarray | None:
        with self._lock:
            if not self._saving:
                return None
            self._saving = False
            if not self._chunks:
                return None
            audio = np.concatenate(self._chunks, axis=0).flatten().astype(np.float32)
            self._chunks = []
        return audio

    def is_recording(self) -> bool:
        return self._saving

    def snapshot(self) -> np.ndarray | None:
        """Copy of the audio captured so far without stopping. For streaming."""
        with self._lock:
            if not self._chunks:
                return None
            return np.concatenate(self._chunks, axis=0).flatten().astype(np.float32)
