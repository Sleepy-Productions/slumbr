"""Transcript → output-text dispatch, keyed on the active mode's formatter.

Each :class:`~slumbr.config.ModeProfile` carries a ``formatter`` discriminator.
This is the single place that maps it to a concrete text transform, so the app
only ever calls :func:`format_transcript` and never branches on the mode itself.

  * ``"prose"`` → :func:`slumbr.polish.polish` — sentences for Notes / LLM chat.
  * ``"code"``  → :func:`slumbr.code_grammar.format_code` — source for Code mode.

Unknown formatters fall back to prose so a hand-edited config can't break paste.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .code_grammar import format_code
from .polish import polish

if TYPE_CHECKING:
    from .config import ModeProfile


def format_transcript(text: str, profile: ModeProfile) -> str:
    replacements = profile.word_replacements or None
    if profile.formatter == "code":
        return format_code(
            text,
            replacements,
            strip_filler=profile.strip_trailing_filler,
        )
    # "prose" and anything unrecognized.
    return polish(
        text,
        replacements=replacements,
        strip_filler=profile.strip_trailing_filler,
    )
