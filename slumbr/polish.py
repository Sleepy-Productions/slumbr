"""Lightweight post-processing for Whisper / Moonshine transcripts.

The recognizer returns punctuated, capitalized text *most* of the time —
but for short utterances ("hello can you hear me") it sometimes drops
both, it invents polite trailers on trailing silence, and it mis-spells
the user's jargon the same way every time. This module normalizes the
final paste text:

1. Strip Whisper's end-of-clip hallucinations ("Thank you.", "Thanks for
   watching.") — only the curated set below, and only when real content
   precedes them, so a genuine short "thank you" survives.
2. Apply the user's find-replace map (consistent mishears → corrections).
   Whole-word, case-insensitive, backend-agnostic.
3. Capitalize the first letter + the first letter after each sentence end.
4. Append a terminal period if the text doesn't already end in `.!?`
   (or `,;:`, left alone in case the user is mid-clause).

We intentionally do NOT insert commas/question marks or fix grammar —
those need real NLP and the failure modes are worse than the miss.
"""

from __future__ import annotations

import re

_AFTER_TERMINAL = re.compile(r"([.!?]\s+)([a-z])")
_TERMINATORS = frozenset(".!?,;:")

# ---- runaway-repetition guard --------------------------------------------
# ASR models can spiral into emitting one phrase over and over, destroying
# the real content — especially Moonshine on long audio, which (unlike the
# faster-whisper engine) has no anti-repetition decode knobs. Observed
# 2026-05-25: a 122 s dictation came out as "...the green for a while, it's
# like a color theme. And then..." ~60 times, the actual words gone. This
# collapses a block of words that repeats consecutively down to one copy.
# Tuned to NEVER touch natural speech: only multi-word phrases repeated 4+
# times, or a single word repeated 6+ times ("you you you you you you"), get
# collapsed — real emphasis ("no no no", "really really") is left alone.
_REPEAT_MAX_PERIOD = 16        # longest phrase (in words) we scan for looping
_REPEAT_MIN_REPS_PHRASE = 4    # collapse a >=2-word block at this many reps
_REPEAT_MIN_REPS_WORD = 6      # single-word block needs more (spare emphasis)
_REPEAT_MIN_WORDS = 8          # below this the text is too short to be a loop


def _norm_word(w: str) -> str:
    return w.lower().strip(".,!?;:\"'()-")


def _collapse_repetition(text: str) -> str:
    """Collapse consecutively-repeated word blocks (runaway ASR loops)."""
    words = text.split()
    if len(words) < _REPEAT_MIN_WORDS:
        return text
    changed = True
    # Re-run until stable so multi-scale loops (a period-12 run, then a
    # residual period-7 tail) both collapse.
    while changed:
        changed = False
        norm = [_norm_word(w) for w in words]
        n = len(words)
        for p in range(1, _REPEAT_MAX_PERIOD + 1):
            min_reps = _REPEAT_MIN_REPS_WORD if p == 1 else _REPEAT_MIN_REPS_PHRASE
            out: list[str] = []
            i = 0
            collapsed = False
            while i < n:
                reps = 1
                while (
                    i + (reps + 1) * p <= n
                    and norm[i + reps * p : i + (reps + 1) * p] == norm[i : i + p]
                ):
                    reps += 1
                if reps >= min_reps:
                    out.extend(words[i : i + p])  # keep a single copy
                    i += reps * p
                    collapsed = True
                else:
                    out.append(words[i])
                    i += 1
            if collapsed:
                words = out
                changed = True
                break  # restart the scan on the shrunk text
    return " ".join(words)

# Canonical Whisper trailing hallucinations (lowercased, no trailing
# punctuation). These are phrases the model emits on silence/music at the
# end of a clip — they're almost never something the user dictated mid-
# session. Matched only at the very end of the text.
_TRAILING_FILLER: tuple[str, ...] = (
    "thanks for watching",
    "thank you for watching",
    "thank you for listening",
    "thanks for listening",
    "please subscribe",
    "don't forget to subscribe",
    "like and subscribe",
    "see you next time",
    "thank you",
    "thank you.",
)

# Only strip a trailer if at least this many words remain afterward — keeps
# a deliberate short "thank you" message from being erased.
_MIN_WORDS_AFTER_STRIP = 4

_TRAILING_RE = re.compile(
    r"\s*[,.!?\-\s]*(?:" + "|".join(re.escape(p.rstrip(".")) for p in _TRAILING_FILLER) + r")[\s.!?]*$",
    re.IGNORECASE,
)


def _strip_trailing_filler(text: str) -> str:
    m = _TRAILING_RE.search(text)
    if not m:
        return text
    head = text[: m.start()].rstrip()
    # Don't strip if the whole utterance basically IS the filler.
    if len(head.split()) < _MIN_WORDS_AFTER_STRIP:
        return text
    return head


def _apply_replacements(text: str, replacements: dict[str, str]) -> str:
    for heard, corrected in replacements.items():
        heard = heard.strip()
        if not heard:
            continue
        # Whole-word, case-insensitive. \b doesn't play nice with multi-word
        # keys, so guard with lookaround on word chars instead.
        pattern = re.compile(rf"(?<!\w){re.escape(heard)}(?!\w)", re.IGNORECASE)
        # Replace via a function so ``corrected`` is taken LITERALLY — a string
        # replacement would interpret backslash escapes / group refs (a user
        # typing "C:\1" or a trailing "\" would crash re.sub or corrupt output).
        text = pattern.sub(lambda _m: corrected, text)
    return text


def polish(
    text: str,
    *,
    replacements: dict[str, str] | None = None,
    strip_filler: bool = True,
) -> str:
    text = text.strip()
    if not text:
        return text

    # Collapse runaway repetition first — it's the most destructive failure
    # and shrinking it makes the rest of the pass cheaper + cleaner.
    text = _collapse_repetition(text)
    if strip_filler:
        text = _strip_trailing_filler(text).strip()
    if replacements:
        text = _apply_replacements(text, replacements).strip()
    if not text:
        return text

    text = text[0].upper() + text[1:]
    text = _AFTER_TERMINAL.sub(
        lambda m: m.group(1) + m.group(2).upper(),
        text,
    )

    if text[-1] not in _TERMINATORS:
        text = text + "."

    return text
