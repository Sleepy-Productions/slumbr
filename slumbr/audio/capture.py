from __future__ import annotations

import threading

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "float32"
BLOCKSIZE = 1024


class AudioRecorder:
    """Mono 16 kHz float32 capture via sounddevice (WASAPI on Windows by default).

    `start()` opens an InputStream whose callback appends numpy chunks to an
    internal buffer. `stop()` closes the stream and returns the concatenated
    1-D float32 array. Returns `None` if nothing was captured.

    The callback runs on the PortAudio thread — keep it lean. No logging
    beyond xrun warnings; do not paint or touch UI from here.
    """

    def __init__(self, samplerate: int = SAMPLE_RATE, channels: int = CHANNELS) -> None:
        self.samplerate = samplerate
        self.channels = channels
        self._stream: sd.InputStream | None = None
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()

    def _callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        if status:
            print(f"[audio] xrun/status: {status}")
        with self._lock:
            self._chunks.append(indata.copy())

    def start(self) -> None:
        if self._stream is not None:
            return
        with self._lock:
            self._chunks = []
        self._stream = sd.InputStream(
            samplerate=self.samplerate,
            channels=self.channels,
            dtype=DTYPE,
            blocksize=BLOCKSIZE,
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> np.ndarray | None:
        if self._stream is None:
            return None
        try:
            self._stream.stop()
            self._stream.close()
        finally:
            self._stream = None
        with self._lock:
            if not self._chunks:
                return None
            audio = np.concatenate(self._chunks, axis=0).flatten().astype(np.float32)
            self._chunks = []
        return audio

    def is_recording(self) -> bool:
        return self._stream is not None
