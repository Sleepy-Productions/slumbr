"""Transcript history — the current session's recent dictations.

Ephemeral by default: entries live in memory only, never touch disk, and vanish
the moment Slumbr closes. The list is a rolling window of at most
``MAX_ENTRIES`` — a new transcript past the cap drops the *oldest* entry, so you
always keep your most recent dictations (it never wipes the whole list).

Optional persistence (off by default): if the user opts in via
``configure(persist=True)``, transcripts are ALSO written to a local SQLite file
(see ``history_store``) so they survive restarts. Turning it back off deletes
that file — the ephemeral-by-default privacy story is preserved.

The tray shows the latest entry; the Settings → History tab shows the list and
lets you copy any line (or all). "Clear history" empties it immediately (and the
on-disk store too, when persistence is on).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

# Cap on the live in-memory list. It's a ROLLING window: past the cap the oldest
# entry is dropped (not the whole list), so recent dictations are always kept.
# Sized generously so a heavy session's history is here for troubleshooting.
MAX_ENTRIES = 200

# Session-scoped, in-memory only. Appended on the Qt main thread; read from the
# tray thread (latest()) — a stale read there is harmless (just the menu label).
_entries: list[HistoryEntry] = []

# Whether transcripts are also persisted to disk. OFF by default; flipped by
# ``configure()`` from the saved config / the Settings toggle.
_persist = False


@dataclass
class HistoryEntry:
    text: str
    ts: float  # unix seconds; rendered as "2 min ago" / "13:42" in the UI


def configure(persist: bool) -> None:
    """Set whether transcripts persist to disk. Call at startup with the saved
    config, and whenever the user toggles the setting.

    Turning ON: any entries already in memory are written to the store first,
    then the full on-disk history is loaded so past sessions show immediately.
    This prevents losing in-memory transcripts when the user enables persistence
    mid-session.

    Turning OFF: deletes the store file, leaving no trace."""
    global _persist
    was = _persist
    _persist = bool(persist)
    if _persist and not was:
        from . import history_store

        # Snapshot current in-memory entries before loading from disk so we
        # can write them back after merging (fix: mid-session enable must not
        # discard entries the user has already dictated this session).
        current_entries = list(_entries)

        # Load what's already persisted.
        rows = history_store.load_recent(MAX_ENTRIES)
        persisted = [HistoryEntry(text=t, ts=ts) for t, ts in rows]

        # Merge: combine persisted + current in-memory, dedupe by (text, ts),
        # keep chronological order, truncate to MAX_ENTRIES newest.
        seen: set[tuple[str, float]] = set()
        merged: list[HistoryEntry] = []
        for e in persisted + current_entries:
            key = (e.text, e.ts)
            if key not in seen:
                seen.add(key)
                merged.append(e)
        merged.sort(key=lambda e: e.ts)
        if len(merged) > MAX_ENTRIES:
            merged = merged[-MAX_ENTRIES:]

        _entries.clear()
        _entries.extend(merged)

        # Persist any in-memory entries that were not already on disk.
        persisted_keys = {(t, ts) for t, ts in rows}
        for e in current_entries:
            if (e.text, e.ts) not in persisted_keys:
                history_store.add(e.text, e.ts)

    elif was and not _persist:
        from . import history_store

        history_store.delete_file()


def load_all() -> list[HistoryEntry]:
    """Snapshot of this session's entries, oldest-first."""
    return list(_entries)


def latest() -> str:
    """The most recent transcript, or empty string if none."""
    return _entries[-1].text if _entries else ""


def append(text: str) -> None:
    """Add a transcript. The list is a rolling window: past ``MAX_ENTRIES`` the
    oldest entry is dropped, so the most recent dictations are always kept.
    Empty / whitespace-only inputs are ignored (no polluting history with
    debouncer artifacts from accidental hotkey taps). When persistence is on the
    transcript is also written to the on-disk store."""
    text = text.strip()
    if not text:
        return
    entry = HistoryEntry(text=text, ts=time.time())
    _entries.append(entry)
    if len(_entries) > MAX_ENTRIES:
        del _entries[0]  # rolling: drop the oldest, keep the recent ones
    if _persist:
        from . import history_store

        history_store.add(entry.text, entry.ts)


def clear_memory() -> None:
    """Empty only the in-memory list, leaving the on-disk store intact.

    Use this on shutdown/restart so a user who opted into persistent history
    doesn't lose their stored transcripts just because Slumbr exited cleanly.
    The on-disk store is the user's data; clearing memory is an implementation
    detail of process lifecycle, not a user action.
    """
    _entries.clear()


def clear() -> None:
    """Empty the history immediately (Settings → Clear history button).
    When persistence is on, the on-disk store is wiped too.

    This is the full wipe triggered by explicit user action. Do NOT call this
    on normal shutdown/restart — use ``clear_memory()`` instead so the
    persisted store survives.
    """
    _entries.clear()
    if _persist:
        from . import history_store

        history_store.clear()
