"""Transcript history — the current *partial* batch, at ``%APPDATA%\\Slumbr\\history.jsonl``.

Replaces the "last transcript" surface that used to live on the deleted
``HomePanel``. The tray menu shows the latest entry as a non-clickable
header item; the Settings dialog's History tab shows this live batch.

This holds at most ``MAX_ENTRIES`` transcripts — the live view. When it
fills, the full batch *rolls* into a temporary session log (see
``session_logs.py``) and the live view resets to fresh, so the History tab
never grows past ``MAX_ENTRIES`` and you get a clean slate. The rolled
batches stay recoverable from "Session logs" until Slumbr closes. History
itself is session-scoped: ``app.py`` clears it on a clean launch.

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

from . import session_logs

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
    """Append a transcript to the live batch.

    When the live batch is already full, the whole thing rolls into a session
    log and the view resets — so this new transcript becomes ``1 / MAX_ENTRIES``
    of a fresh batch and the rolled 30 stay recoverable from Session logs.

    Empty / whitespace-only inputs are ignored (don't pollute history with
    debouncer artifacts from accidental hotkey taps).
    """
    text = text.strip()
    if not text:
        return

    entries = load_all()
    if len(entries) >= MAX_ENTRIES:
        # Full — archive this batch and start fresh. The live view never
        # exceeds MAX_ENTRIES; the rolled batch is recoverable via Session logs.
        session_logs.roll_batch(entries)
        entries = []
    entries.append(HistoryEntry(text=text, ts=time.time()))
    _write(entries)


def _write(entries: list[HistoryEntry]) -> None:
    """Atomically (temp + os.replace) rewrite the live batch file."""
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
