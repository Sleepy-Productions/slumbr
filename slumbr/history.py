"""In-memory transcript history — the current session's recent dictations.

Deliberately ephemeral: entries live in memory only, never touch disk, and
vanish the moment Slumbr closes. The list holds at most ``MAX_ENTRIES``; when a
new transcript would exceed that, the whole list is cleared and the new one
starts a fresh list. No session logs, no recovery files, nothing written to
disk — your dictations exist only for this session.

The tray shows the latest entry; the Settings → History tab shows the list and
lets you copy any line (or all). "Clear history" empties it immediately.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

# Cap on the live list. Reaching it wipes the list (the new entry starts fresh),
# so History never grows past this and nothing is persisted.
MAX_ENTRIES = 50

# Session-scoped, in-memory only. Appended on the Qt main thread; read from the
# tray thread (latest()) — a stale read there is harmless (just the menu label).
_entries: list[HistoryEntry] = []


@dataclass
class HistoryEntry:
    text: str
    ts: float  # unix seconds; rendered as "2 min ago" / "13:42" in the UI


def load_all() -> list[HistoryEntry]:
    """Snapshot of this session's entries, oldest-first."""
    return list(_entries)


def latest() -> str:
    """The most recent transcript, or empty string if none."""
    return _entries[-1].text if _entries else ""


def append(text: str) -> None:
    """Add a transcript. At the cap the list clears (gone — no log kept) and
    this transcript starts a fresh list, so the view never exceeds
    ``MAX_ENTRIES``. Empty / whitespace-only inputs are ignored (no polluting
    history with debouncer artifacts from accidental hotkey taps)."""
    text = text.strip()
    if not text:
        return
    if len(_entries) >= MAX_ENTRIES:
        _entries.clear()
    _entries.append(HistoryEntry(text=text, ts=time.time()))


def clear() -> None:
    """Empty the history immediately (Settings → Clear history, and on quit)."""
    _entries.clear()
