"""Live popup partials via Moonshine + Silero VAD + interim re-decode.

Architecture (May 2026)
------------------------

Popup partials and the final paste run on different engines because they
have fundamentally different jobs:

- The **final paste** uses Whisper large-v3 / large-v3-turbo on CUDA via
  faster-whisper. Non-streaming. Best-in-class accuracy. See
  `slumbr/stt/engine.py`.
- The **live popup partials** (this file) use **Moonshine base int8** on
  CPU, with TWO decoding paths to give the user the smooth word-by-word
  feel they want while still landing on accurate punctuated text:
    1. **Interim decode**, on a background worker thread, every ~300 ms.
       Re-runs Moonshine on the audio-so-far-this-phrase to produce a
       live partial. This is what makes the popup feel "buttery" — text
       appears as you speak rather than only after you pause.
    2. **Silero-VAD-bounded finalization.** When VAD detects a natural
       pause (~500 ms of silence) it finalizes a phrase. We run
       Moonshine on the precisely-cut audio and pass the result through
       an **online punctuation + truecasing** model. That finalized,
       properly cased phrase replaces the interim text for that phrase
       and is committed into the running transcript.

Why a worker thread for interim decode:
  - feed() is called from the Qt main thread (via a queued signal from
    the PortAudio thread). Moonshine base int8 takes ~50–150 ms per
    decode on a modern CPU; running that synchronously inside feed()
    would freeze the popup waveform every 300 ms.
  - The worker has a single-slot inbox: each new submission overwrites
    any pending request, so the worker always decodes the freshest
    audio. The recognizer is shared between worker and main-thread
    drain via `_decoder_lock`.

Why Moonshine + this architecture vs the previous LibriSpeech Zipformer:
  - The 2023 Zipformer streamed word-by-word but its real-world accuracy
    was poor — the popup text often didn't match what the user said.
    Moonshine is trained on ~300 K hours of varied web speech and is
    materially more accurate.
  - This re-decode-the-tail pattern would have flickered horribly with
    Whisper (which freely flips case + punctuation pass-to-pass), but
    Moonshine's decoder is stable enough run-to-run that the streamed
    interim text grows monotonically in practice.

Tradeoff the caller should know:
  - The interim text is best-effort (lowercase, sometimes off by a word
    at chunk boundaries). The finalized phrase that lands at each VAD
    boundary is the source of truth — properly cased + punctuated.

Models downloaded on first launch (cached at `%APPDATA%\\Slumbr\\models`):
  - Moonshine base int8 (~180 MB unpacked) from
    `csukuangfj/sherpa-onnx-moonshine-base-en-int8` on Hugging Face.
  - Silero VAD ONNX (~2 MB) from snakers4/silero-vad on GitHub.
  - Online punctuation + truecasing (~30 MB) from a sherpa-onnx GitHub
    release tarball.
"""

from __future__ import annotations

import logging
import os
import tarfile
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import sherpa_onnx

from .._bundled import bundled_models_root

log = logging.getLogger(__name__)


@dataclass
class StreamingPartial:
    """Two-state view of the current transcript for the popup.

    The popup draws `committed` chars at full opacity and `tentative`
    chars at a dimmer opacity. As LocalAgreement-2 promotes tentative
    words to committed, characters cross the boundary and brighten.
    """

    committed: str
    tentative: str

    @property
    def full(self) -> str:
        if not self.tentative:
            return self.committed
        if not self.committed:
            return self.tentative
        return f"{self.committed} {self.tentative}"


SAMPLE_RATE = 16000

_MODELS_ROOT = Path(os.path.expandvars(r"%APPDATA%\Slumbr\models"))

# --- Moonshine int8 ONNX bundles (HuggingFace, sherpa-onnx zoo) ----------
# Two variants ship in the sherpa-onnx zoo: base (accurate, ~180 MB) and
# tiny (fastest, ~80 MB). sherpa-onnx has NO small/medium Moonshine
# bundle (verified 2026-05-25 — the landscape doc's "Moonshine Medium"
# was wrong). Both bundles expose the identical 5-file layout, so only
# the repo + local cache dir differ. Streaming partials always use base;
# the CPU primary backend lets the user pick either (backends/moonshine.py).
_MOONSHINE_FILES = [
    "preprocess.onnx",
    "encode.int8.onnx",
    "cached_decode.int8.onnx",
    "uncached_decode.int8.onnx",
    "tokens.txt",
]
_MOONSHINE_VARIANTS: dict[str, tuple[str, Path]] = {
    "base": ("csukuangfj/sherpa-onnx-moonshine-base-en-int8", _MODELS_ROOT / "moonshine-base-en"),
    "tiny": ("csukuangfj/sherpa-onnx-moonshine-tiny-en-int8", _MODELS_ROOT / "moonshine-tiny-en"),
}
# Back-compat aliases — base is the default wherever a variant isn't given.
_MOONSHINE_HF, _MOONSHINE_DIR = _MOONSHINE_VARIANTS["base"]

# --- Silero VAD (raw GitHub) ---------------------------------------------
_VAD_DIR = _MODELS_ROOT / "silero-vad"
_VAD_FILENAME = "silero_vad.onnx"
_VAD_URL = (
    "https://raw.githubusercontent.com/snakers4/silero-vad/master/"
    "src/silero_vad/data/silero_vad.onnx"
)

# --- Online punctuation (sherpa-onnx GitHub release tarball) -------------
_PUNCT_DIR = _MODELS_ROOT / "online-punct-en"
_PUNCT_TARBALL_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "punctuation-models/sherpa-onnx-online-punct-en-2024-08-06.tar.bz2"
)
_PUNCT_ARCHIVE_PREFIX = "sherpa-onnx-online-punct-en-2024-08-06"
_PUNCT_MODEL_FILE = "model.int8.onnx"
_PUNCT_VOCAB_FILE = "bpe.vocab"


class ModelDownloadError(RuntimeError):
    pass


# ---------------------------------------------------------- download helpers


def _download_url(url: str, dest: Path) -> None:
    """Stream-download `url` to `dest`. Atomic via .partial swap."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".partial")
    log.info("downloading %s", url)
    with urllib.request.urlopen(url) as resp, open(tmp, "wb") as fh:
        while True:
            block = resp.read(1 << 16)
            if not block:
                break
            fh.write(block)
    tmp.replace(dest)


def _ensure_moonshine(variant: str = "base") -> dict[str, str]:
    """Download a Moonshine int8 ONNX bundle from HF if missing.

    ``variant`` is "base" (default, accurate ~180 MB) or "tiny" (fastest
    ~80 MB). Both share the same 5-file layout; only the repo + cache dir
    differ. Unknown variants fall back to base rather than failing.
    """
    repo, mdir = _MOONSHINE_VARIANTS.get(variant, _MOONSHINE_VARIANTS["base"])
    if all((mdir / f).is_file() for f in _MOONSHINE_FILES):
        return {f: str(mdir / f) for f in _MOONSHINE_FILES}

    bundled = bundled_models_root()
    if bundled is not None:
        bdir = bundled / mdir.name  # e.g. "moonshine-base-en"
        if all((bdir / f).is_file() for f in _MOONSHINE_FILES):
            log.info("using bundled Moonshine %s from %s", variant, bdir)
            return {f: str(bdir / f) for f in _MOONSHINE_FILES}

    log.info("downloading Moonshine %s int8 to %s", variant, mdir)
    mdir.mkdir(parents=True, exist_ok=True)
    from huggingface_hub import snapshot_download

    snapshot_download(
        repo_id=repo,
        local_dir=str(mdir),
        allow_patterns=_MOONSHINE_FILES,
    )
    missing = [f for f in _MOONSHINE_FILES if not (mdir / f).is_file()]
    if missing:
        raise ModelDownloadError(f"Moonshine {variant} files missing after download: {missing}")
    return {f: str(mdir / f) for f in _MOONSHINE_FILES}


def _ensure_vad() -> str:
    target = _VAD_DIR / _VAD_FILENAME
    if target.is_file():
        return str(target)
    bundled = bundled_models_root()
    if bundled is not None:
        b = bundled / "silero-vad" / _VAD_FILENAME
        if b.is_file():
            log.info("using bundled Silero VAD from %s", b)
            return str(b)
    log.info("downloading Silero VAD (~2 MB) to %s", _VAD_DIR)
    _download_url(_VAD_URL, target)
    return str(target)


def _ensure_punct() -> tuple[str, str] | None:
    """Download the online punct tarball and extract the int8 model + vocab.

    Returns None on any failure — punctuation is optional and we want the
    rest of the engine to function even if this download fails.
    """
    model_path = _PUNCT_DIR / _PUNCT_MODEL_FILE
    vocab_path = _PUNCT_DIR / _PUNCT_VOCAB_FILE
    if model_path.is_file() and vocab_path.is_file():
        return str(model_path), str(vocab_path)

    bundled = bundled_models_root()
    if bundled is not None:
        bm = bundled / "online-punct-en" / _PUNCT_MODEL_FILE
        bv = bundled / "online-punct-en" / _PUNCT_VOCAB_FILE
        if bm.is_file() and bv.is_file():
            log.info("using bundled online-punct from %s", bm.parent)
            return str(bm), str(bv)

    try:
        log.info("downloading online punctuation model (~30 MB) to %s", _PUNCT_DIR)
        _PUNCT_DIR.mkdir(parents=True, exist_ok=True)
        tarball = _PUNCT_DIR / "punct.tar.bz2"
        _download_url(_PUNCT_TARBALL_URL, tarball)
        with tarfile.open(tarball, "r:bz2") as tf:
            for member in tf.getmembers():
                # Members look like "sherpa-onnx-online-punct-en-2024-08-06/model.int8.onnx".
                # Flatten — extract just the files we need into _PUNCT_DIR.
                name = Path(member.name).name
                if name in {_PUNCT_MODEL_FILE, _PUNCT_VOCAB_FILE} and member.isfile():
                    member.name = name
                    tf.extract(member, _PUNCT_DIR)
        tarball.unlink(missing_ok=True)
    except Exception as e:  # noqa: BLE001
        log.warning("punctuation model download/extract failed: %s", e)
        return None
    if not (model_path.is_file() and vocab_path.is_file()):
        log.warning("punctuation files missing after extract")
        return None
    return str(model_path), str(vocab_path)


# ---------------------------------------------------------- builders


# ONNX Runtime execution provider for the streaming-popup models. CUDA
# is the right default: during *recording* (when streaming runs) Whisper
# isn't using the GPU — Whisper only fires after the user taps stop —
# so the earlier "no contention" justification doesn't apply. CUDA drops
# Moonshine decode from ~100 ms to ~20-30 ms on RTX-class GPUs. If the
# sherpa-onnx wheel wasn't built with the CUDA EP, builders silently
# fall back to CPU and log a warning so the rest of the app keeps
# working.
_DEFAULT_PROVIDER = "cuda"


def _build_recognizer(
    num_threads: int, provider: str = _DEFAULT_PROVIDER
) -> sherpa_onnx.OfflineRecognizer:
    files = _ensure_moonshine()
    try:
        return sherpa_onnx.OfflineRecognizer.from_moonshine(
            preprocessor=files["preprocess.onnx"],
            encoder=files["encode.int8.onnx"],
            uncached_decoder=files["uncached_decode.int8.onnx"],
            cached_decoder=files["cached_decode.int8.onnx"],
            tokens=files["tokens.txt"],
            num_threads=num_threads,
            decoding_method="greedy_search",
            provider=provider,
        )
    except Exception as e:  # noqa: BLE001
        if provider != "cpu":
            log.warning(
                "Moonshine on provider=%r failed (%s); falling back to CPU",
                provider,
                e,
            )
            return _build_recognizer(num_threads, provider="cpu")
        raise


def _build_vad(num_threads: int) -> sherpa_onnx.VoiceActivityDetector:
    # VAD inference is sub-millisecond on CPU; CUDA overhead would
    # outweigh the speedup, so we keep this on CPU regardless of
    # _DEFAULT_PROVIDER.
    vad_model = _ensure_vad()
    vad_config = sherpa_onnx.VadModelConfig()
    vad_config.silero_vad.model = vad_model
    # Tuned for tap-to-stop dictation:
    #   - threshold 0.5 (Silero default) -> 0.4: catch quiet trailing
    #     syllables so we don't clip the end of phrases.
    #   - min_silence_duration 500 ms: short enough that phrase boundaries
    #     land quickly; long enough not to split within a phrase that has
    #     a slight breath pause.
    #   - min_speech_duration 200 ms: drop blips < 200 ms (mic thumps).
    vad_config.silero_vad.threshold = 0.4
    # Lowered from 500 ms to 300 ms: tap-to-stop dictation has explicit
    # phrase boundaries (the user releases when they're done), so
    # finalizing on slightly shorter pauses doesn't risk splitting
    # within a phrase the way it would in always-on streaming, and the
    # earlier finalization commits text to the popup ~200 ms sooner.
    vad_config.silero_vad.min_silence_duration = 0.3
    vad_config.silero_vad.min_speech_duration = 0.2
    vad_config.silero_vad.max_speech_duration = 30.0
    vad_config.sample_rate = SAMPLE_RATE
    vad_config.num_threads = num_threads
    return sherpa_onnx.VoiceActivityDetector(vad_config, buffer_size_in_seconds=60.0)


def _build_punct(num_threads: int) -> sherpa_onnx.OnlinePunctuation | None:
    paths = _ensure_punct()
    if paths is None:
        return None
    cnn_bilstm, bpe_vocab = paths
    model_cfg = sherpa_onnx.OnlinePunctuationModelConfig()
    model_cfg.cnn_bilstm = cnn_bilstm
    model_cfg.bpe_vocab = bpe_vocab
    model_cfg.num_threads = num_threads
    model_cfg.provider = "cpu"
    cfg = sherpa_onnx.OnlinePunctuationConfig(model_cfg)
    return sherpa_onnx.OnlinePunctuation(cfg)


# ---------------------------------------------------------- engine


# How often the worker checks its inbox while idle. Mostly irrelevant —
# submissions explicitly set the wakeup event.
_WORKER_IDLE_WAIT_S = 1.0

# Adaptive interim re-decode cadence. With Moonshine on CUDA, decode is
# ~20-30 ms for short tails (vs ~100 ms on CPU), so we can push the
# first-tier cadence much faster. The single-slot inbox in
# `_InterimDecoder` means overlapping submits just overwrite — no queue
# pile-up — so being aggressive here is safe.
_INTERIM_CADENCE_TIERS = (
    (1.0, 0.08),  # in-progress < 1 s   →  80 ms cadence (word-by-word feel)
    (3.0, 0.15),  # in-progress < 3 s   → 150 ms cadence
    (float("inf"), 0.25),  # otherwise  → 250 ms cadence
)

# Minimum and maximum amount of in-progress audio (in seconds) we will
# pass to interim decoder. Below `min`, decoding wastes CPU because there
# is nothing to recognize yet. Above `max`, decoding gets expensive and
# the user is almost certainly about to hit a natural pause anyway.
# Min lowered to 100 ms. Moonshine produces usable first words from a
# single syllable's worth of audio; tentatives at 100 ms may flicker
# more, but LocalAgreement-2 covers that. The win is ~100 ms earlier
# first-tentative — visible in the speech-relative latency log.
_INTERIM_MIN_AUDIO_S = 0.10
_INTERIM_MAX_AUDIO_S = 6.00

# Force-commit a stuck tentative tail if LocalAgreement-2 has held the
# same disagreement at the same prefix length for this long. Lowered
# from 3 s to 1.5 s — most speech repairs resolve in 1-2 words, so
# beyond that the model is probably stuck and we should move on.
_LA2_TENTATIVE_TIMEOUT_S = 1.5


def _interim_cadence_s(in_progress_s: float) -> float:
    for cap, cadence in _INTERIM_CADENCE_TIERS:
        if in_progress_s < cap:
            return cadence
    return _INTERIM_CADENCE_TIERS[-1][1]


class _LocalAgreement2:
    """LocalAgreement-2 (Macháček et al. 2023) over whitespace words.

    On each interim decode, we compare the new word sequence against the
    previous one and treat the longest common *prefix* as freshly
    committed for the current phrase. Anything past the LCP stays
    tentative until two consecutive passes agree on it.

    Operating on whitespace-split words rather than model tokens avoids
    depending on whether `sherpa_onnx.OfflineRecognizerResult` exposes
    `.tokens` for the Moonshine binding — research surfaced conflicting
    reports. English word boundaries are stable enough across Moonshine
    passes that word-level LA-2 catches the same "stable prefix vs
    flickering tail" signal as token-level would.
    """

    def __init__(self, timeout_s: float = _LA2_TENTATIVE_TIMEOUT_S) -> None:
        self._timeout_s = timeout_s
        self.reset()

    def reset(self) -> None:
        self._prev_words: list[str] = []
        self._committed_words: list[str] = []
        self._stuck_since: float | None = None
        self._stuck_committed_len: int = 0

    @property
    def committed(self) -> str:
        return " ".join(self._committed_words)

    def feed(self, text: str) -> tuple[str, str]:
        """Feed a fresh interim text. Returns (committed, tentative)."""
        words = text.split()

        # Longest common prefix of the two most recent passes.
        prev = self._prev_words
        lcp_len = 0
        for a, b in zip(prev, words, strict=False):
            if a == b:
                lcp_len += 1
            else:
                break
        lcp_words = words[:lcp_len]

        # Extend committed iff the new LCP both starts with the existing
        # committed prefix AND is longer than it. Never shrink — past
        # commits stay committed even if the model walks back later.
        if (
            len(lcp_words) > len(self._committed_words)
            and lcp_words[: len(self._committed_words)] == self._committed_words
        ):
            self._committed_words = lcp_words

        # Tentative tail: the portion of this pass after the committed
        # prefix. If the pass no longer starts with the committed words
        # (model rewrote what we already promoted — rare for word-level
        # LA-2 on stable models), show the un-matched portion of this
        # pass as tentative; the committed prefix stays visible.
        if words[: len(self._committed_words)] == self._committed_words:
            tentative_words = words[len(self._committed_words) :]
        else:
            tentative_words = words[len(self._committed_words) :]

        # Stuck-tail watchdog. If committed length hasn't grown for
        # `_timeout_s`, force-commit the current tentative tail. Keeps
        # the popup moving instead of freezing on a long mumble.
        now = time.monotonic()
        if len(self._committed_words) > self._stuck_committed_len:
            self._stuck_committed_len = len(self._committed_words)
            self._stuck_since = now
        elif self._stuck_since is None:
            self._stuck_since = now
        elif now - self._stuck_since >= self._timeout_s and tentative_words:
            log.debug(
                "LA-2 force-commit after %.1fs stuck on tentative=%r",
                self._timeout_s,
                tentative_words,
            )
            self._committed_words.extend(tentative_words)
            tentative_words = []
            self._stuck_committed_len = len(self._committed_words)
            self._stuck_since = now

        self._prev_words = words
        return " ".join(self._committed_words), " ".join(tentative_words)


class _InterimDecoder:
    """Background worker that decodes the latest in-progress audio.

    Single-slot inbox: `submit(audio)` overwrites any pending request, so
    the worker always picks up the freshest audio rather than queueing.
    The decoder is shared with the main-thread VAD drain via
    `decoder_lock`.
    """

    def __init__(
        self,
        recognizer: sherpa_onnx.OfflineRecognizer,
        punct: sherpa_onnx.OnlinePunctuation | None,
        decoder_lock: threading.Lock,
    ) -> None:
        self._recognizer = recognizer
        self._punct = punct
        self._decoder_lock = decoder_lock
        self._inbox_lock = threading.Lock()
        self._pending: np.ndarray | None = None
        self._latest_text: str = ""
        self._wakeup = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="slumbr-interim", daemon=True)
        self._thread.start()

    def submit(self, audio: np.ndarray) -> None:
        with self._inbox_lock:
            self._pending = audio
        self._wakeup.set()

    def latest(self) -> str:
        with self._inbox_lock:
            return self._latest_text

    def clear(self) -> None:
        with self._inbox_lock:
            self._latest_text = ""
            self._pending = None

    def stop(self) -> None:
        self._stop.set()
        self._wakeup.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wakeup.wait(_WORKER_IDLE_WAIT_S)
            self._wakeup.clear()
            if self._stop.is_set():
                break
            with self._inbox_lock:
                audio = self._pending
                self._pending = None
            if audio is None:
                continue
            try:
                text = self._decode(audio)
            except Exception as e:  # noqa: BLE001
                log.debug("interim decode failed: %s", e)
                continue
            with self._inbox_lock:
                self._latest_text = text

    def _decode(self, audio: np.ndarray) -> str:
        t0 = time.perf_counter()
        with self._decoder_lock:
            stream = self._recognizer.create_stream()
            stream.accept_waveform(SAMPLE_RATE, audio)
            self._recognizer.decode_stream(stream)
            text = (stream.result.text or "").strip()
        decode_ms = (time.perf_counter() - t0) * 1000.0
        if not text:
            log.debug("interim decode (empty): %.1fms", decode_ms)
            return ""
        # Punctuate the interim too. We tried skipping this for ~30 ms
        # of savings per pass, but the format mismatch between
        # unpunctuated lowercase interim text and the punctuated +
        # capitalized text from VAD-finalized segments caused the popup
        # to hard-reset at every phrase boundary: LCP collapsed to 0,
        # so the diff treated the finalized text as completely new and
        # typewriter-swept it from the top-left. Matching formats keeps
        # the LCP non-zero across the transition.
        punct_ms = 0.0
        if self._punct is not None:
            tp = time.perf_counter()
            try:
                text = self._punct.add_punctuation_with_case(text)
            except Exception as e:  # noqa: BLE001
                log.debug("interim punct failed: %s", e)
            punct_ms = (time.perf_counter() - tp) * 1000.0
        log.debug(
            "interim decode: %.1fms + punct %.1fms on %.2fs -> %r",
            decode_ms,
            punct_ms,
            len(audio) / SAMPLE_RATE,
            text[:60],
        )
        return text.strip()


class StreamingASREngine:
    """Live partials with both interim word-by-word + VAD-bounded finals.

    Lifecycle
    ---------
        engine.start_session()         # begin a new utterance
        engine.feed(chunk_np)          # push 16 kHz mono float32 audio;
                                       # returns the partial transcript so far
        engine.end_session()           # flush + return final partial

    `feed()` is non-blocking — it pushes to the VAD, opportunistically
    submits the in-progress audio to the interim worker, and returns the
    current best transcript (finalized segments + latest interim). The
    interim decoder runs on its own thread so the Qt main thread never
    blocks on a Moonshine pass.
    """

    def __init__(
        self,
        num_threads: int = 8,
        enable_streaming_leading_edge: bool = False,
    ) -> None:
        log.info("loading Moonshine base + Silero VAD + online punctuation...")
        self._recognizer = _build_recognizer(num_threads)
        self._vad = _build_vad(num_threads)
        self._punct = _build_punct(num_threads)
        self._decoder_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._segments: list[str] = []
        self._in_progress: list[np.ndarray] = []
        self._last_interim_submit: float = 0.0
        self._la2 = _LocalAgreement2()
        self._last_la2_input: str = ""
        self._zipformer_text: str = ""
        self._interim = _InterimDecoder(self._recognizer, self._punct, self._decoder_lock)

        # Session latency tracking. Two t0 markers:
        #   - `_session_t0`: wall-clock at start_session (tap moment).
        #     Includes the user's reaction time before speaking.
        #   - `_speech_t0`: wall-clock when VAD first detected speech
        #     this session. Isolates *model* latency from reaction time.
        # We log both deltas for each first-tentative and first-committed
        # event so we can tune against the right number.
        self._session_t0: float = 0.0
        self._speech_t0: float | None = None
        self._first_tentative_logged: bool = False
        self._first_committed_logged: bool = False

        # Optional append-only streaming model (LibriSpeech-trained
        # Zipformer) that drives the popup's tentative tail. Off unless
        # explicitly enabled; failures fall back silently to
        # Moonshine-only since the leading edge is a UX nicety, not
        # essential.
        self._zipformer = None
        if enable_streaming_leading_edge:
            try:
                from .streaming_zipformer import StreamingZipformer

                self._zipformer = StreamingZipformer(num_threads=2)
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "streaming leading edge unavailable, falling back to "
                    "Moonshine-only partials: %s",
                    e,
                )
        log.info(
            "streaming engine ready (punctuation=%s, leading_edge=%s)",
            self._punct is not None,
            self._zipformer is not None,
        )

    # ----------------------------------------------------- session API
    def start_session(self) -> None:
        with self._state_lock:
            self._segments = []
            self._in_progress = []
            self._last_interim_submit = 0.0
            self._la2.reset()
            self._last_la2_input = ""
            self._zipformer_text = ""
            self._vad.reset()
            self._session_t0 = time.monotonic()
            self._speech_t0 = None
            self._first_tentative_logged = False
            self._first_committed_logged = False
        self._interim.clear()
        if self._zipformer is not None:
            try:
                self._zipformer.start_session()
            except Exception as e:  # noqa: BLE001
                log.warning("zipformer start_session failed: %s", e)

    def feed(self, chunk: np.ndarray) -> StreamingPartial:
        """Push audio in. Returns the current best (committed, tentative)."""
        if chunk.ndim > 1:
            chunk = chunk.reshape(-1)
        chunk = chunk.astype(np.float32, copy=False)

        # Drive the leading-edge Zipformer outside the state lock — it
        # has its own internal lock and we don't want to serialize the
        # two ASR engines with each other.
        if self._zipformer is not None:
            try:
                self._zipformer_text = self._zipformer.feed(chunk)
            except Exception as e:  # noqa: BLE001
                log.debug("zipformer feed failed: %s", e)

        with self._state_lock:
            self._vad.accept_waveform(chunk)
            self._drain_finalized_segments()
            self._track_in_progress(chunk)
            self._maybe_submit_interim()
            return self._compose_partial()

    def end_session(self) -> StreamingPartial:
        """Flush VAD, recognize any trailing segment, return final partial."""
        with self._state_lock:
            try:
                self._vad.flush()
            except Exception as e:  # noqa: BLE001
                log.warning("vad.flush failed: %s", e)
            self._drain_finalized_segments()
            # Stop accepting new interim updates — the result of the last
            # interim pass might still arrive after this call, but
            # start_session() will clear it before the next utterance.
            self._in_progress = []
            self._interim.clear()
            self._la2.reset()
            self._last_la2_input = ""
            self._zipformer_text = ""
            partial = self._compose_partial()
        if self._zipformer is not None:
            try:
                self._zipformer.end_session()
            except Exception as e:  # noqa: BLE001
                log.debug("zipformer end_session failed: %s", e)
        return partial

    def shutdown(self) -> None:
        """Stop the background interim worker. Call once on app exit."""
        self._interim.stop()

    # ----------------------------------------------------- internals
    def _drain_finalized_segments(self) -> None:
        finalized_any = False
        while not self._vad.empty():
            seg = self._vad.front
            samples = np.asarray(seg.samples, dtype=np.float32)
            self._vad.pop()
            if samples.size < int(0.15 * SAMPLE_RATE):
                continue
            text = self._recognize_segment(samples)
            if text:
                self._segments.append(text)
                finalized_any = True
        # A VAD finalization means the phrase is now in self._segments and
        # any in-progress audio belongs to a *new* upcoming phrase. Drop
        # the buffer + interim guess and reset LA-2 so the next phrase
        # starts fresh.
        if finalized_any:
            self._in_progress = []
            self._interim.clear()
            self._la2.reset()
            self._last_la2_input = ""

    def _track_in_progress(self, chunk: np.ndarray) -> None:
        # Only buffer audio when VAD thinks the user is currently speaking.
        # Pre-speech silence and post-speech silence would only hurt the
        # interim decode's accuracy.
        try:
            is_speech = bool(self._vad.is_speech_detected())
        except Exception:  # noqa: BLE001
            is_speech = True
        if not is_speech:
            return
        # Capture speech-onset timestamp the first time VAD flags speech
        # in this session. This is the "tap" moment that isolates model
        # latency from the user's reaction time.
        if self._speech_t0 is None and self._session_t0 > 0.0:
            self._speech_t0 = time.monotonic()
            log.info(
                "streaming: speech detected at t+%.0fms",
                (self._speech_t0 - self._session_t0) * 1000.0,
            )
        self._in_progress.append(chunk)
        # Clip the in-progress buffer at the interim-decode ceiling — there
        # is no point keeping more audio than we'd actually re-decode.
        total_samples = sum(c.size for c in self._in_progress)
        cap = int(_INTERIM_MAX_AUDIO_S * SAMPLE_RATE)
        while total_samples > cap and len(self._in_progress) > 1:
            dropped = self._in_progress.pop(0)
            total_samples -= dropped.size

    def _maybe_submit_interim(self) -> None:
        if not self._in_progress:
            return
        total_samples = sum(c.size for c in self._in_progress)
        if total_samples < int(_INTERIM_MIN_AUDIO_S * SAMPLE_RATE):
            return
        in_progress_s = total_samples / SAMPLE_RATE
        cadence = _interim_cadence_s(in_progress_s)
        now = time.monotonic()
        if now - self._last_interim_submit < cadence:
            return
        audio = np.concatenate(self._in_progress).astype(np.float32, copy=False)
        self._last_interim_submit = now
        self._interim.submit(audio)

    def _recognize_segment(self, samples: np.ndarray) -> str:
        t0 = time.perf_counter()
        with self._decoder_lock:
            stream = self._recognizer.create_stream()
            stream.accept_waveform(SAMPLE_RATE, samples)
            self._recognizer.decode_stream(stream)
            text = (stream.result.text or "").strip()
        decode_ms = (time.perf_counter() - t0) * 1000.0
        if not text:
            log.debug(
                "segment decode (empty): %.1fms on %.2fs",
                decode_ms,
                len(samples) / SAMPLE_RATE,
            )
            return ""
        punct_ms = 0.0
        if self._punct is not None:
            tp = time.perf_counter()
            try:
                text = self._punct.add_punctuation_with_case(text)
            except Exception as e:  # noqa: BLE001
                log.debug("punctuation pass failed: %s", e)
            punct_ms = (time.perf_counter() - tp) * 1000.0
        log.debug(
            "segment decode: %.1fms + punct %.1fms on %.2fs -> %r",
            decode_ms,
            punct_ms,
            len(samples) / SAMPLE_RATE,
            text[:80],
        )
        return text.strip()

    def _compose_partial(self) -> StreamingPartial:
        finalized = " ".join(p for p in self._segments if p).strip()

        # Pull the latest interim text and feed LA-2 *only* when it has
        # actually changed since the previous feed. Repeated identical
        # feeds would still produce correct text but would muddy the
        # stuck-tail watchdog timer.
        interim_text = self._interim.latest().strip()
        if interim_text and interim_text != self._last_la2_input:
            self._la2.feed(interim_text)
            self._last_la2_input = interim_text

        committed_phrase = self._la2.committed if self._in_progress else ""
        committed_words = committed_phrase.split()
        tentative_phrase = ""

        if self._in_progress:
            # Tentative source preference: if the streaming Zipformer is
            # active AND its current text begins with the LA-2 committed
            # prefix (case-insensitive word match), use its trailing words
            # as the visible tail. That gives the user the fast append-
            # only feel even between Moonshine re-decodes. If Zipformer
            # disagrees with what LA-2 committed, fall back to the
            # Moonshine interim's tail so we don't paint contradictory
            # text.
            zf_words = (self._zipformer_text or "").split()
            use_zipformer = (
                self._zipformer is not None
                and len(zf_words) > len(committed_words)
                and [w.lower() for w in zf_words[: len(committed_words)]]
                == [w.lower() for w in committed_words]
            )
            if use_zipformer:
                tentative_phrase = " ".join(zf_words[len(committed_words) :])
            elif interim_text:
                interim_words = interim_text.split()
                tentative_phrase = " ".join(interim_words[len(committed_words) :])

        # Compose committed text. Finalized segments concat with the
        # LA-2-committed prefix of the current in-progress phrase.
        if finalized and committed_phrase:
            committed = f"{finalized} {committed_phrase}"
        else:
            committed = finalized or committed_phrase

        # Latency instrumentation. Log once per session the delay to
        # first-tentative and first-committed, measured from BOTH:
        #   - session_t0 (tap moment, includes reaction time)
        #   - speech_t0 (VAD speech-onset, isolates model latency)
        # The speech-relative number is the one to tune against; the
        # session-relative one captures the actual user experience.
        if self._session_t0 > 0.0:
            now = time.monotonic()
            if tentative_phrase and not self._first_tentative_logged:
                session_ms = (now - self._session_t0) * 1000.0
                speech_ms = (now - self._speech_t0) * 1000.0 if self._speech_t0 is not None else 0.0
                log.info(
                    "streaming: first tentative tap+%.0fms speech+%.0fms",
                    session_ms,
                    speech_ms,
                )
                self._first_tentative_logged = True
            if committed and not self._first_committed_logged:
                session_ms = (now - self._session_t0) * 1000.0
                speech_ms = (now - self._speech_t0) * 1000.0 if self._speech_t0 is not None else 0.0
                log.info(
                    "streaming: first committed tap+%.0fms speech+%.0fms",
                    session_ms,
                    speech_ms,
                )
                self._first_committed_logged = True

        return StreamingPartial(committed=committed, tentative=tentative_phrase)
