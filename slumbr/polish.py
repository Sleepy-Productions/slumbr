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
        text = pattern.sub(corrected, text)
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
