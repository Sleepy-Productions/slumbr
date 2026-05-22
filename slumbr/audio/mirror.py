"""Mic-to-virtual-cable routing — the universal reverse-PTT path.

The Discord PTM hack (``slumbr/input/mute_key.py``) only works for apps
that expose a keybind-based mute. This module is the real solution:

  - User installs `VB-Audio Virtual Cable <https://vb-audio.com/Cable/>`_
    (free, ~5 MB, one-time install).
  - User configures Discord / Zoom / Teams / OBS / anything else to use
    "CABLE Output" as their microphone (instead of the real HyperX
    directly).
  - Slumbr opens TWO audio streams: a capture from the real mic
    (already exists via ``AudioRecorder``) and a render targeting
    "CABLE Input" — Slumbr passes the real mic audio through to the
    cable in real time.
  - Other apps see Slumbr-via-cable as their mic. They never touch the
    real HyperX directly.
  - When Slumbr enters its RECORDING state, ``set_muted(True)`` switches
    the passthrough to write *silence* into the cable — other apps
    hear nothing. Slumbr's own real-mic capture continues unaffected
    and Whisper transcribes normally.
  - Exit RECORDING → ``set_muted(False)`` → other apps hear the user again.

Why "write silence" rather than "stop writing":
  An OutputStream with no writes underruns. WASAPI's response to
  underrun is unpredictable (some apps interpret as glitch, others
  insert their own filler). Writing zeros keeps the stream continuous
  and the timing aligned — silence is what we mean.

Latency:
  The chain adds ~20–50 ms of latency to the user's voice in their call
  app (PortAudio input buffer + Python hop + PortAudio output buffer).
  Acceptable for voice; users with strict latency requirements would
  prefer a kernel-level virtual driver, which is out of scope.
"""

from __future__ import annotations

import logging
import threading

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)


# Substrings that flag a sounddevice output as a virtual cable. Ordered
# by how aggressively we should auto-pick — VB-Cable's canonical name is
# "CABLE Input", and that's what we want first if the user has multiple
# routing tools installed.
_VIRTUAL_CABLE_KEYWORDS: tuple[str, ...] = (
    "cable input",   # VB-Audio Virtual Cable — primary
    "vb-cable",      # alternate label some Windows installs use
    "vb-audio",      # broader VB family (Voicemeeter etc.)
    "voicemeeter",   # Voicemeeter's virtual inputs
    "virtual cable", # generic fallback
)


def find_virtual_cables() -> list[tuple[int, str]]:
    """Scan sounddevice for output devices whose names suggest virtual
    cables. Returns ``[(device_index, device_name), …]`` ordered by
    preference (VB-Cable first if present).

    Empty list = the user hasn't installed any virtual-audio software.
    The Settings UI surfaces that state with a help link.
    """
    out: list[tuple[int, str, int]] = []  # index, name, priority
    try:
        for i, d in enumerate(sd.query_devices()):
            if int(d.get("max_output_channels", 0)) <= 0:
                continue
            name = str(d.get("name", ""))
            name_lc = name.lower()
            for prio, kw in enumerate(_VIRTUAL_CABLE_KEYWORDS):
                if kw in name_lc:
                    out.append((i, name, prio))
                    break
    except Exception as e:  # noqa: BLE001
        log.warning("sounddevice query for virtual cables failed: %s", e)
    out.sort(key=lambda t: t[2])
    return [(idx, name) for idx, name, _ in out]


class MicMirror:
    """Streams mic samples into a virtual cable output device, with a
    boolean ``muted`` gate. All public methods are safe to call from
    any thread.

    ``push()`` is called on the PortAudio INPUT thread (via
    ``AudioRecorder.on_chunk``). It writes synchronously to the
    OutputStream — this is fine because the OutputStream's buffer is
    sized at default (~50 ms) so writes typically return in microseconds.
    A stalled output (e.g. cable device disappeared) will eventually
    drop chunks on the input side, which manifests as glitchy capture
    rather than a hung process. We log + degrade rather than retry.
    """

    def __init__(
        self,
        output_device: int | str,
        *,
        samplerate: int = 16000,
        channels: int = 1,
    ) -> None:
        self._device = output_device
        self._samplerate = samplerate
        self._channels = channels
        self._stream: sd.OutputStream | None = None
        # Default to NOT muted: when the user enables routing, their
        # voice should flow into calls normally. Mute happens only
        # during dictation.
        self._muted = False
        self._lock = threading.Lock()
        self._silence_cache: np.ndarray | None = None  # reused zero buffer

    def start(self) -> None:
        """Open the OutputStream and begin accepting pushes."""
        if self._stream is not None:
            return
        try:
            self._stream = sd.OutputStream(
                device=self._device,
                samplerate=self._samplerate,
                channels=self._channels,
                dtype="float32",
            )
            self._stream.start()
            log.info(
                "MicMirror started device=%r samplerate=%d channels=%d",
                self._device, self._samplerate, self._channels,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("MicMirror open failed for device=%r: %s", self._device, e)
            self._stream = None
            raise

    def stop(self) -> None:
        """Close the OutputStream. Idempotent."""
        if self._stream is None:
            return
        try:
            self._stream.stop()
            self._stream.close()
        except Exception as e:  # noqa: BLE001
            log.debug("MicMirror stop raised: %s", e)
        self._stream = None
        log.info("MicMirror stopped")

    def push(self, samples: np.ndarray) -> None:
        """Forward ``samples`` (or silence, if muted) into the cable.

        Called from the PortAudio input thread — must stay fast and
        must never raise back into the caller.
        """
        if self._stream is None:
            return
        with self._lock:
            muted = self._muted
        if muted:
            # Reuse a single silence buffer of matching shape to avoid
            # allocating a fresh zero array on every audio chunk
            # (~50 Hz cadence under default capture settings).
            if self._silence_cache is None or self._silence_cache.shape != samples.shape:
                self._silence_cache = np.zeros_like(samples)
            payload = self._silence_cache
        else:
            payload = samples.astype(np.float32, copy=False)
        try:
            self._stream.write(payload)
        except sd.PortAudioError as e:
            # Stream got into a bad state — most likely the cable device
            # vanished (user changed default audio in Windows mid-call).
            # Don't take down the input thread with us; just stop and
            # log so the next config-change tick can re-open.
            log.warning("MicMirror write failed (stream will close): %s", e)
            self.stop()
        except Exception as e:  # noqa: BLE001
            log.debug("MicMirror write raised: %s", e)

    def set_muted(self, muted: bool) -> None:
        """Toggle the silence gate. Lock-protected because this is
        called from the Qt main thread while ``push()`` is called from
        the PortAudio thread."""
        with self._lock:
            if self._muted == muted:
                return
            self._muted = muted
        log.debug("MicMirror muted=%s", muted)

    @property
    def is_running(self) -> bool:
        return self._stream is not None
