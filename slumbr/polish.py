"""Lightweight post-processing for Whisper transcripts.

Whisper's `large-v3-turbo` returns punctuated, capitalized text *most* of
the time — but for short utterances ("hello can you hear me") it
sometimes drops both. This module normalizes the output so paste-target
text always reads as a proper sentence:

- First alphabetic character is uppercased.
- Letters that follow a sentence-ending mark (`.!?`) + whitespace are
  uppercased too.
- A terminal period is appended if the text doesn't already end in
  `.!?` (or `,;:`, which we leave alone in case the user is dictating a
  clause they're about to extend).

We intentionally do NOT try to insert commas, question marks, or fix
grammar — those need real NLP and the failure modes are worse than the
miss. This is the minimum that makes Whisper's quiet utterances feel
finished.
"""

from __future__ import annotations

import re

_AFTER_TERMINAL = re.compile(r"([.!?]\s+)([a-z])")
_TERMINATORS = frozenset(".!?,;:")


def polish(text: str) -> str:
    text = text.strip()
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
