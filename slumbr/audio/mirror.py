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
import re
import threading

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)

# Matches a channel-count token like "16ch" / "8 ch". VB-Audio exposes each
# cable twice: a plain stereo endpoint ("CABLE Input …") and a multichannel
# sibling ("CABLE In 16ch …" — note the driver truncates "Input"→"In", so the
# names don't share a base). We treat ANY "<N>ch" entry as the sibling.
_CHANNEL_SUFFIX_RE = re.compile(r"\b\d+\s*ch\b", re.IGNORECASE)


def _select_cables(
    candidates: list[tuple[int, str, int, int]],
) -> list[tuple[int, str]]:
    """From priority-sorted ``(idx, name, kw_prio, host_prio)`` candidates pick
    the cables to surface: prefer the plain stereo endpoints and HIDE the
    multichannel ``<N>ch`` siblings — they're the same cable, add channels
    Slumbr's mono passthrough never uses, and just clutter the picker. Fall back
    to the ``<N>ch`` entries only if NO plain endpoint exists (so routing still
    works on installs that expose only the multichannel one). Dedupe by name."""
    plain = [(i, n) for (i, n, _k, _h) in candidates if not _CHANNEL_SUFFIX_RE.search(n)]
    pool = plain if plain else [(i, n) for (i, n, _k, _h) in candidates]
    out: list[tuple[int, str]] = []
    seen: set[str] = set()
    for idx, name in pool:
        if name in seen:
            continue
        seen.add(name)
        out.append((idx, name))
    return out


# Substrings that flag a sounddevice output as a virtual cable. Ordered
# by how aggressively we should auto-pick — VB-Cable's canonical name is
# "CABLE Input", and that's what we want first if the user has multiple
# routing tools installed.
_VIRTUAL_CABLE_KEYWORDS: tuple[str, ...] = (
    "cable input",  # VB-Audio Virtual Cable — primary
    "vb-cable",  # alternate label some Windows installs use
    "vb-audio",  # broader VB family (Voicemeeter etc.)
    "voicemeeter",  # Voicemeeter's virtual inputs
    "virtual cable",  # generic fallback
)


# Windows audio host APIs ranked per direction.
#
# For OUTPUT (MicMirror writing to virtual cable): prefer WASAPI for
# lowest latency, DirectSound as a fallback. BOTH MME and WDM-KS are
# EXCLUDED (priority 99):
#   - MME truncates names at 31 chars → confuses the dropdown UI.
#   - WDM-KS rejects PortAudio's blocking ``stream.write()`` with
#     "Unanticipated host error … 'Blocking API not supported yet'
#     [-9999]". MicMirror does blocking writes, so a WDM-KS-only device
#     (e.g. "Output (VB-Audio Point)", which exposes no WASAPI/DSound
#     endpoint) can NEVER work as a routing target — surfacing it just
#     produces a device the user picks and then watches fail. Excluding
#     it means find_virtual_cables() won't offer it and
#     resolve_device_index() returns None for a saved WDM-KS-only name,
#     so the app cleanly reports "routing unavailable" instead of looping
#     on -9999. (Observed 2026-05-25: ~9 repeated open failures.)
#
# For INPUT (AudioRecorder reading mic): prefer DirectSound + MME
# because they go through Windows's kernel mixer which transparently
# resamples between Slumbr's requested 16 kHz mono and whatever the
# device's hardware mix format is. WASAPI shared mode rejects a
# 16 kHz mono request on devices whose mix format is fixed at e.g.
# 192 kHz stereo (HyperX QuadCast 2 S) with paInvalidDevice [-9996].
_OUTPUT_HOST_API_PRIORITY: dict[str, int] = {
    "Windows WASAPI": 0,
    "Windows DirectSound": 1,
    "Windows WDM-KS": 99,  # excluded — no blocking-write support (-9999)
    "MME": 99,  # excluded — truncated names confuse the dropdown
}
_INPUT_HOST_API_PRIORITY: dict[str, int] = {
    "Windows DirectSound": 0,
    "MME": 1,
    "Windows WDM-KS": 2,
    "Windows WASAPI": 3,  # last — strict mix-format check
}
# Back-compat alias for callers that imported the old single map
# (find_virtual_cables uses output priorities since it only looks at
# output devices).
_HOST_API_PRIORITY = _OUTPUT_HOST_API_PRIORITY


def find_virtual_cables() -> list[tuple[int, str]]:
    """Scan sounddevice for output devices whose names suggest virtual
    cables. Returns ``[(device_index, device_name), …]`` ordered by
    preference (canonical VB-Cable on WASAPI first if present).

    Empty list = the user hasn't installed any virtual-audio software.
    The Settings UI surfaces that state with the auto-install button.

    Sounddevice enumerates the same physical device once per host API
    (MME / DirectSound / WASAPI), so a single VB-Cable install yields
    3+ device entries. We dedupe by *normalized* name and keep only
    the highest-priority host's entry for each. MME entries are
    excluded entirely because they truncate names at 31 chars and
    would look like distinct devices.
    """
    candidates: list[tuple[int, str, int, int]] = []  # idx, name, kw_prio, host_prio
    try:
        for i, d in enumerate(sd.query_devices()):
            if int(d.get("max_output_channels", 0)) <= 0:
                continue
            name = str(d.get("name", "")).strip()
            if not name:
                continue
            name_lc = name.lower()
            kw_prio: int | None = None
            for j, kw in enumerate(_VIRTUAL_CABLE_KEYWORDS):
                if kw in name_lc:
                    kw_prio = j
                    break
            if kw_prio is None:
                continue
            try:
                hostapi_name = sd.query_hostapis(d.get("hostapi", 0))["name"]
            except (KeyError, IndexError):
                hostapi_name = ""
            host_prio = _HOST_API_PRIORITY.get(hostapi_name, 50)
            if host_prio >= 99:
                continue  # skip MME and unknowns
            candidates.append((i, name, kw_prio, host_prio))
    except Exception as e:  # noqa: BLE001
        log.warning("sounddevice query for virtual cables failed: %s", e)

    # Sort by keyword priority first (canonical "CABLE Input" wins over
    # 16ch variant), then host-API priority (WASAPI wins over DSound).
    candidates.sort(key=lambda t: (t[2], t[3]))

    # Prefer the plain stereo endpoints, hide the "<N>ch" multichannel siblings
    # (see _select_cables) so the picker shows one obvious entry per cable.
    return _select_cables(candidates)


def resolve_device_index(name: str, *, want_input: bool = False) -> int | None:
    """Return the device index whose name matches ``name`` exactly,
    preferring WASAPI > DirectSound > WDM-KS over (excluded) MME.

    sounddevice rejects ambiguous-by-name device lookups when the same
    name exists across host APIs — and VB-Cable's "CABLE Input" /
    "CABLE Output" each appear under three of them. This helper
    resolves to a specific index so callers can pass an unambiguous
    device id to ``sd.InputStream`` / ``sd.OutputStream``.

    Also tolerates MME's 31-char name truncation: if no full-name match
    exists in higher-priority host APIs, fall back to any device whose
    name *starts with* the given prefix (truncated entries on top of
    a full-name WASAPI device).

    ``want_input=True`` looks for input devices (max_input_channels > 0);
    default False looks for output devices.
    """
    target = name.strip()
    field = "max_input_channels" if want_input else "max_output_channels"
    priority_map = _INPUT_HOST_API_PRIORITY if want_input else _OUTPUT_HOST_API_PRIORITY
    candidates: list[tuple[int, int]] = []  # (idx, host_prio)
    fallback: list[tuple[int, int]] = []  # truncation-tolerant fallback

    try:
        devices = list(sd.query_devices())
    except Exception as e:  # noqa: BLE001
        log.warning("sd.query_devices in resolve_device_index failed: %s", e)
        return None

    for i, d in enumerate(devices):
        if int(d.get(field, 0)) <= 0:
            continue
        dev_name = str(d.get("name", "")).strip()
        try:
            hostapi_name = sd.query_hostapis(d.get("hostapi", 0))["name"]
        except (KeyError, IndexError):
            hostapi_name = ""
        host_prio = priority_map.get(hostapi_name, 50)
        if host_prio >= 99:
            continue  # skipped host API (MME for outputs)
        if dev_name == target:
            candidates.append((i, host_prio))
        elif dev_name.startswith(target) or target.startswith(dev_name):
            # Truncation-tolerant fallback (MME truncates at 31 chars
            # so a saved truncated name needs to match its full-name
            # counterpart, or vice versa).
            fallback.append((i, host_prio))

    if candidates:
        candidates.sort(key=lambda t: t[1])
        return candidates[0][0]
    if fallback:
        fallback.sort(key=lambda t: t[1])
        return fallback[0][0]
    return None


class MicMirror:
    """Streams mic samples into a virtual cable output device, with a
    boolean ``muted`` gate. All public methods are safe to call from
    any thread — ``self._lock`` guards the *entire* stream lifecycle
    (open / write / close), not just the mute gate.

    Why the lock spans ``write()``: ``push()`` runs on the PortAudio
    INPUT thread while ``stop()`` runs on the Qt main thread. Before,
    only ``_muted`` was locked, so a ``stop()`` (or push()'s own error
    handler) could ``close()`` the native stream *between* push()'s
    ``self._stream is not None`` check and its ``self._stream.write()``
    call — a use-after-free on a freed PortAudio handle, which crashes
    the interpreter natively with no Python traceback. (Observed
    2026-05-25: "MicMirror write failed … Stream is stopped
    [-9983]" immediately before a silent process death.) Holding the
    lock across the write serializes close-vs-write so the handle can't
    be freed mid-write. Writes normally return in microseconds; a
    stalled/dead stream raises promptly (it doesn't hang), so the worst
    case is a few ms of added latency on a concurrent ``set_muted`` —
    cheap insurance against a hard crash.
    """

    def __init__(
        self,
        output_device: int | str,
        *,
        samplerate: int = 16000,
        channels: int = 1,
    ) -> None:
        # Accept either a device name (resolved to index, preferring
        # WASAPI) or a raw int index. Storing the resolved int lets us
        # bypass sounddevice's host-API ambiguity rejection AND lets
        # ``_reconcile_mic_mirror`` compare against the original name
        # for "device changed" detection.
        if isinstance(output_device, str):
            self._device_name = output_device
            resolved = resolve_device_index(output_device)
            if resolved is None:
                raise ValueError(
                    f"no output device named {output_device!r} found "
                    "(maybe the virtual cable was uninstalled?)"
                )
            self._device: int = resolved
        else:
            self._device = int(output_device)
            self._device_name = ""
        # ``samplerate`` / ``channels`` are the SOURCE format Slumbr
        # provides (16 kHz mono). The OUTPUT stream's format is
        # discovered from the device's preferred mix format — VB-Cable
        # under WASAPI shared mode only accepts 48 kHz stereo and
        # rejects anything else with paInvalidDevice. We upsample +
        # duplicate channels at push() time to match.
        self._src_samplerate = samplerate
        try:
            info = sd.query_devices(self._device)
            self._dst_samplerate = int(info.get("default_samplerate") or samplerate)
            dst_ch = int(info.get("max_output_channels", channels))
            self._dst_channels = max(channels, min(dst_ch, 2))  # cap at stereo
        except Exception as e:  # noqa: BLE001
            log.warning(
                "could not query device %r for native format (%s); using requested",
                self._device,
                e,
            )
            self._dst_samplerate = samplerate
            self._dst_channels = channels
        # Upsampling ratio. WASAPI usually wants 48 kHz, so 48000/16000=3.
        # Integer-only multiples are fine for cable routing — quality is
        # downstream of the call app's own resampling anyway.
        if self._dst_samplerate <= self._src_samplerate:
            self._upsample = 1
        else:
            self._upsample = max(1, self._dst_samplerate // self._src_samplerate)
        self._stream: sd.OutputStream | None = None
        # Default to NOT muted: when the user enables routing, their
        # voice should flow into calls normally. Mute happens only
        # during dictation.
        self._muted = False
        self._lock = threading.Lock()
        self._silence_cache: np.ndarray | None = None  # reused zero buffer

    def start(self) -> None:
        """Open the OutputStream and begin accepting pushes."""
        with self._lock:
            if self._stream is not None:
                return
            try:
                stream = sd.OutputStream(
                    device=self._device,
                    samplerate=self._dst_samplerate,
                    channels=self._dst_channels,
                    dtype="float32",
                )
                stream.start()
                self._stream = stream
                log.info(
                    "MicMirror started device=%d (%r) -> %d Hz %d ch (src %d Hz %d ch, x%d upsample)",
                    self._device,
                    self._device_name or "?",
                    self._dst_samplerate,
                    self._dst_channels,
                    self._src_samplerate,
                    1,
                    self._upsample,
                )
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "MicMirror open failed for device=%d (%r): %s",
                    self._device,
                    self._device_name or "?",
                    e,
                )
                self._stream = None
                raise

    def _close_stream_locked(self) -> None:
        """Stop + close the stream and clear the handle. Idempotent.

        The caller MUST already hold ``self._lock`` — this is the shared
        teardown used by both the public ``stop()`` and push()'s error
        path, so it never re-acquires the (non-reentrant) lock.
        """
        if self._stream is None:
            return
        try:
            self._stream.stop()
            self._stream.close()
        except Exception as e:  # noqa: BLE001
            log.debug("MicMirror stop raised: %s", e)
        self._stream = None
        log.info("MicMirror stopped")

    def stop(self) -> None:
        """Close the OutputStream. Idempotent, thread-safe."""
        with self._lock:
            self._close_stream_locked()

    def push(self, samples: np.ndarray) -> None:
        """Forward ``samples`` (or silence, if muted) into the cable.

        Called from the PortAudio input thread — must stay fast and
        must never raise back into the caller. Adapts 16 kHz mono
        ``samples`` to the device's native format (typically 48 kHz
        stereo for VB-Cable) via nearest-neighbor upsampling +
        channel duplication. Quality "good enough for voice" — the
        call app does its own resampling on the other side.

        The whole body holds ``self._lock`` so the native stream handle
        can't be closed out from under ``write()`` (see class docstring).
        """
        with self._lock:
            stream = self._stream
            if stream is None:
                return
            muted = self._muted
            # 1) Pick source samples (real or silence).
            if muted:
                if self._silence_cache is None or self._silence_cache.shape != samples.shape:
                    self._silence_cache = np.zeros_like(samples)
                src = self._silence_cache
            else:
                src = samples.astype(np.float32, copy=False)
            # 2) Flatten to 1-D so upsample + tile produce predictable
            # shapes regardless of whether ``samples`` came in (N,) or (N,1).
            if src.ndim > 1:
                src = src.reshape(-1)
            # 3) Nearest-neighbor upsample if the device wants a higher rate.
            if self._upsample > 1:
                src = np.repeat(src, self._upsample)
            # 4) Duplicate to stereo if the device wants more channels.
            if self._dst_channels >= 2:
                payload = np.column_stack([src] * self._dst_channels).astype(np.float32, copy=False)
            else:
                payload = src
            try:
                stream.write(payload)
            except sd.PortAudioError as e:
                # Stream got into a bad state — most likely the cable device
                # vanished (user changed default audio in Windows mid-call).
                # Don't take down the input thread with us; close in-place
                # (lock already held) so the next config-change tick re-opens.
                log.warning("MicMirror write failed (stream will close): %s", e)
                self._close_stream_locked()
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
        with self._lock:
            return self._stream is not None
