"""Tests for MicMirror.push — the upsample + channel-tile + mute-gate logic.

All tests here are pure-arithmetic: no audio device is opened. We patch
``sounddevice.query_devices`` and ``sounddevice.OutputStream`` so the
constructor and start() both succeed without hardware.

Load-bearing invariants exercised:
- Mono 16 kHz → stereo 48 kHz upsample + tile (the canonical VB-Cable path).
- Muted frame: payload is all-zeros regardless of input samples.
- Silence cache is reused (not reallocated) across muted frames of the same shape.
- No-op when stream is None (MicMirror not yet started).
- Unstarted push does not raise.
"""

from __future__ import annotations

import threading

import numpy as np
import pytest

from slumbr.audio.mirror import MicMirror


# ---------------------------------------------------------------------------
# Minimal sounddevice stubs (no real audio).


class _FakeOutputStream:
    """Records the last payload written so tests can inspect it."""

    def __init__(self, **_kw):
        self.last_payload: np.ndarray | None = None
        self.started = False
        self.closed = False

    def start(self):
        self.started = True

    def stop(self):
        pass

    def close(self):
        self.closed = True

    def write(self, data: np.ndarray) -> None:
        self.last_payload = data.copy()


@pytest.fixture
def fake_sd(monkeypatch):
    """Patch sounddevice so MicMirror never touches real hardware."""
    import sounddevice as sd

    device_info = {
        "name": "CABLE Input",
        "max_output_channels": 2,
        "default_samplerate": 48000.0,
        "hostapi": 0,
    }
    monkeypatch.setattr(sd, "query_devices", lambda idx=None: device_info)
    stream = _FakeOutputStream()
    monkeypatch.setattr(sd, "OutputStream", lambda **kw: stream)
    return stream


# ---------------------------------------------------------------------------
# Helpers


def _make_mirror(fake_sd) -> MicMirror:
    """Return a started MicMirror wired to the fake stream."""
    m = MicMirror(0, samplerate=16000, channels=1)
    m.start()
    return m


def _samples(n: int = 1024, val: float = 0.5) -> np.ndarray:
    return np.full((n, 1), val, dtype=np.float32)


# ---------------------------------------------------------------------------
# Tests


class TestPushUpsampleAndTile:
    def test_payload_shape_is_stereo_48k(self, fake_sd):
        m = _make_mirror(fake_sd)
        input_frames = 1024  # at 16 kHz
        m.push(_samples(input_frames))
        payload = fake_sd.last_payload
        assert payload is not None
        expected_frames = input_frames * 3  # 16→48 kHz = ×3
        assert payload.shape == (expected_frames, 2), (
            f"expected ({expected_frames}, 2) stereo payload; got {payload.shape}"
        )

    def test_payload_dtype_float32(self, fake_sd):
        m = _make_mirror(fake_sd)
        m.push(_samples(512))
        assert fake_sd.last_payload.dtype == np.float32

    def test_payload_values_preserved(self, fake_sd):
        m = _make_mirror(fake_sd)
        m.push(_samples(512, val=0.75))
        # All samples should be close to 0.75 (no signal processing, just upsample).
        assert np.allclose(fake_sd.last_payload, 0.75, atol=1e-5)

    def test_both_channels_identical(self, fake_sd):
        """Channel duplication: left == right."""
        m = _make_mirror(fake_sd)
        m.push(_samples(256, val=0.3))
        payload = fake_sd.last_payload
        np.testing.assert_array_equal(payload[:, 0], payload[:, 1])


class TestMuteGate:
    def test_muted_payload_is_all_zeros(self, fake_sd):
        m = _make_mirror(fake_sd)
        m.set_muted(True)
        m.push(_samples(512, val=0.9))
        assert np.all(fake_sd.last_payload == 0.0), "muted frame must be silence"

    def test_unmuted_after_mute_passes_signal(self, fake_sd):
        m = _make_mirror(fake_sd)
        m.set_muted(True)
        m.push(_samples(512, val=0.9))
        m.set_muted(False)
        m.push(_samples(512, val=0.4))
        assert not np.all(fake_sd.last_payload == 0.0), "unmuted frame must carry signal"

    def test_silence_cache_reused_across_muted_frames(self, fake_sd):
        """The silence ndarray is allocated once and reused — verify no re-alloc."""
        m = _make_mirror(fake_sd)
        m.set_muted(True)
        m.push(_samples(256))
        cache_id_1 = id(m._silence_cache)
        m.push(_samples(256))  # same shape → same cache object
        cache_id_2 = id(m._silence_cache)
        assert cache_id_1 == cache_id_2, "silence cache must be reused for same shape"

    def test_silence_cache_reallocated_on_shape_change(self, fake_sd):
        """Different block size → cache must be rebuilt."""
        m = _make_mirror(fake_sd)
        m.set_muted(True)
        m.push(_samples(256))
        cache_id_1 = id(m._silence_cache)
        m.push(_samples(512))  # different shape → new cache
        cache_id_2 = id(m._silence_cache)
        assert cache_id_1 != cache_id_2, "silence cache must be rebuilt on shape change"


class TestPushWhenNotStarted:
    def test_push_before_start_does_not_raise(self, fake_sd):
        """MicMirror not started → _stream is None → push is a no-op."""
        m = MicMirror(0, samplerate=16000, channels=1)
        # Do NOT call start() — stream stays None.
        m.push(_samples(512))  # must not raise
        assert fake_sd.last_payload is None

    def test_push_after_stop_does_not_raise(self, fake_sd):
        m = _make_mirror(fake_sd)
        m.stop()
        m.push(_samples(512))  # stream is None after stop → no-op
