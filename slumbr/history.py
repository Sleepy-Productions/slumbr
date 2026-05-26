"""Transcript history — small ring-buffered jsonl at ``%APPDATA%\\Slumbr\\history.jsonl``.

Replaces the "last transcript" surface that used to live on the deleted
``HomePanel``. The tray menu shows the latest entry as a non-clickable
header item; the Settings dialog's History tab shows the last 30.

Why a ring buffer instead of unbounded log: dictation produces 50–200
entries per heavy session. Unbounded growth would let history.jsonl
balloon to MB-scale over time with no value — users care about "what
did I just dictate" not "what did I dictate in March." The buffer keeps
the most recent ``MAX_ENTRIES`` and auto-drops anything older.

Privacy: history stays on disk in plaintext like everything else local.
The Settings dialog has a "Clear history" button. No telemetry.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)


def _history_path() -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) / "Slumbr" if appdata else Path.home() / ".slumbr"
    return base / "history.jsonl"


HISTORY_PATH = _history_path()
MAX_ENTRIES = 30


@dataclass
class HistoryEntry:
    text: str
    ts: float  # unix seconds; rendered as "2 min ago" / "13:42" in the UI

    @classmethod
    def from_json(cls, raw: str) -> HistoryEntry | None:
        try:
            data = json.loads(raw)
            return cls(text=str(data.get("text", "")), ts=float(data.get("ts", 0.0)))
        except (json.JSONDecodeError, ValueError, TypeError):
            return None


# ---------------------------------------------------------- read


def load_all() -> list[HistoryEntry]:
    """Return entries oldest-first. Empty list on any I/O error."""
    if not HISTORY_PATH.is_file():
        return []
    try:
        lines = HISTORY_PATH.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        log.warning("could not read history file: %s", e)
        return []
    entries = [HistoryEntry.from_json(ln) for ln in lines if ln.strip()]
    return [e for e in entries if e is not None]


def latest() -> str:
    """The most recent transcript, or empty string if none."""
    entries = load_all()
    return entries[-1].text if entries else ""


# ---------------------------------------------------------- write


def append(text: str) -> None:
    """Append a transcript and trim to the last ``MAX_ENTRIES``.

    Empty / whitespace-only inputs are ignored (don't pollute history
    with debouncer artifacts from accidental hotkey taps).
    """
    text = text.strip()
    if not text:
        return

    entries = load_all()
    entries.append(HistoryEntry(text=text, ts=time.time()))
    if len(entries) > MAX_ENTRIES:
        entries = entries[-MAX_ENTRIES:]

    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = HISTORY_PATH.with_suffix(".jsonl.tmp")
    try:
        tmp.write_text(
            "\n".join(json.dumps(asdict(e), ensure_ascii=False) for e in entries) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, HISTORY_PATH)
    except OSError as e:
        log.warning("could not write history file: %s", e)


def clear() -> None:
    """Wipe history (used by the Settings dialog's Clear button)."""
    try:
        if HISTORY_PATH.is_file():
            HISTORY_PATH.unlink()
    except OSError as e:
        log.warning("could not clear history file: %s", e)
