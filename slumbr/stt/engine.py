from __future__ import annotations

import logging
import time

import numpy as np
from faster_whisper import WhisperModel

from .._bundled import bundled_models_root

# Re-export TranscriptionError from the protocol module so legacy
# imports of `slumbr.stt.engine.TranscriptionError` keep working.
# The actual definition moved so the worker can use it without
# pulling faster_whisper into the import graph.
from .protocol import TranscriptionError  # noqa: F401

log = logging.getLogger(__name__)


def _resolve_model(model_size: str) -> str:
    """Map a Whisper size name to a bundled local CT2 model dir when one was
    shipped in this frozen build, so first run is fully offline. Falls through
    to the size name (Hugging Face download) for non-frozen runs or sizes that
    weren't bundled."""
    root = bundled_models_root()
    if root is not None:
        local = root / f"whisper-{model_size}"
        if (local / "model.bin").is_file():
            log.info("using bundled Whisper model from %s", local)
            return str(local)
    return model_size


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
        initial_prompt: str = "",
    ) -> None:
        log.info("loading %r on %s (%s)...", model_size, device, compute_type)
        t0 = time.monotonic()
        self.model = WhisperModel(
            _resolve_model(model_size), device=device, compute_type=compute_type
        )
        self.language = language
        self.initial_prompt = initial_prompt or None
        self.model_size = model_size
        log.info("model loaded in %.1fs", time.monotonic() - t0)

    def set_runtime_config(self, *, language: str | None, initial_prompt: str) -> None:
        """Update decode-time knobs without reloading the model.

        Both fields are forwarded to every subsequent `transcribe()` call.
        `language` empty string is treated as "auto-detect" (None).
        """
        self.language = language or None
        self.initial_prompt = initial_prompt or None

    def warm_up(self) -> None:
        log.info("warming up...")
        t0 = time.monotonic()
        silence = np.zeros(int(0.5 * 16000), dtype=np.float32)
        self._run(silence, beam_size=1)
        log.info("warm-up done in %.2fs", time.monotonic() - t0)

    def _run(self, audio: np.ndarray, beam_size: int = 5) -> str:
        # `vad_filter=True` runs Silero VAD over the audio and strips
        # non-speech regions before transcribing. Two wins for our use
        # case:
        # (1) Whisper hallucinations on silence drop dramatically — no
        #     more "thank you" / "you you you" repetitions when the user
        #     pauses.
        # (2) Less audio for the decoder to process, so streaming
        #     partials come back a bit faster too.
        #
        # VAD parameter tuning for tap-to-stop dictation:
        # - threshold 0.5 → 0.35: Silero's default is aggressive about
        #   labeling quiet trailing syllables as non-speech, which clips
        #   the ends of utterances. 0.35 trades a little
        #   silence-leak-through for keeping the user's actual last word.
        # - min_silence_duration_ms 2000 → 800: pauses between phrases in
        #   dictation are rarely 2 s; 800 ms lets VAD segment within an
        #   utterance without splitting at every breath.
        # - speech_pad_ms 400 → 500: extra cushion before/after each
        #   speech region so consonants at word boundaries survive.
        #
        # `initial_prompt` is passed straight through. Whisper uses it as
        # prior context, so words / names / jargon that appear in the
        # prompt are far more likely to be recognized correctly. This is
        # the single highest-leverage accuracy knob for uncommon words.
        # Anti-repetition controls — load-bearing for long dictations.
        # Observed in the prototype logs (2026-05-25): a 97 s utterance
        # decoded in 13.4 s (RTF 0.14, ~4x the 0.035 baseline) and came
        # out garbled — "they're not able to do that because they're not
        # able to do that" repeated five times. That's the classic
        # faster-whisper spiral: with condition_on_previous_text=True
        # (the default), each segment's output is fed back as the prompt
        # for the next, so once the decoder slips into a loop it keeps
        # re-priming itself. The loop also inflates decode time because
        # beam search churns over the repeated tokens — which is why long
        # messages "freeze before they send."
        #
        # - condition_on_previous_text=False: breaks the cross-segment
        #   feedback. Only affects multi-segment (long) audio, so short
        #   utterances — which already transcribe accurately — are
        #   unchanged. This is the highest-leverage knob.
        # - no_repeat_ngram_size=3: hard-blocks regenerating any 3-token
        #   span already emitted. Catches long repeated phrases without
        #   touching natural short repeats ("no no no" is 1-grams; a
        #   4th "really" is the most it will clip).
        # - repetition_penalty=1.15: gentle logit nudge away from loops;
        #   discourages without hard-blocking.
        segments, _info = self.model.transcribe(
            audio,
            language=self.language,
            vad_filter=True,
            vad_parameters={
                "threshold": 0.35,
                "min_silence_duration_ms": 800,
                "speech_pad_ms": 500,
            },
            beam_size=beam_size,
            initial_prompt=self.initial_prompt,
            condition_on_previous_text=False,
            no_repeat_ngram_size=3,
            repetition_penalty=1.15,
        )
        return " ".join(seg.text.strip() for seg in segments).strip()

    def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe with one OOM-aware retry at beam_size=1.

        beam_size=5 is the accuracy-safe default. We briefly ran beam=1
        for ~50% wall-clock savings; in practice the greedy decoder
        loops on short utterances (classic "icon. icon. icon."
        repetition) and the WER hit is unacceptable for dictation.
        """
        t0 = time.monotonic()
        try:
            text = self._run(audio, beam_size=5)
        except RuntimeError as e:
            msg = str(e).lower()
            if "out of memory" in msg or "cublas" in msg or "cudnn" in msg:
                log.warning("CUDA error, retrying at beam_size=1: %s", e)
                try:
                    text = self._run(audio, beam_size=1)
                except Exception as e2:  # noqa: BLE001
                    raise TranscriptionError(f"transcription failed: {e2}") from e2
            else:
                raise TranscriptionError(f"transcription failed: {e}") from e
        dur = time.monotonic() - t0
        audio_s = len(audio) / 16000
        log.info("transcribed %.1fs of audio in %.2fs", audio_s, dur)
        return text
