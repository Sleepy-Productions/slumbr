"""Optional on-disk transcript history (SQLite) — OFF BY DEFAULT.

Slumbr's history is in-memory and ephemeral unless the user explicitly opts in
(Settings → History → "Keep history across restarts"). When enabled, transcripts
are written to an **unencrypted** SQLite database at ``%APPDATA%/Slumbr/history.db``
so they survive restarts. Turning the option back off deletes that file
(``delete_file``), leaving no trace — the privacy-by-default story holds.

This module owns ONLY the disk layer; ``slumbr/history.py`` decides *when* to
call it based on the user's setting and keeps the live in-memory view. Every
operation is best-effort: a failed write/read logs and returns rather than
crashing a dictation.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)

# Hard cap on rows kept on disk — generous for troubleshooting, but bounded so
# the file can't grow without limit. Rows past this are pruned oldest-first.
DB_MAX_ROWS = 5000


def _base_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    return Path(appdata) / "Slumbr" if appdata else Path.home() / ".slumbr"


def db_path() -> Path:
    """Location of the history database (read at call time so tests can point
    ``APPDATA`` at a tmp dir)."""
    return _base_dir() / "history.db"


def _connect() -> sqlite3.Connection:
    p = db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS transcripts ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  text TEXT NOT NULL,"
        "  ts REAL NOT NULL"
        ")"
    )
    return conn


def add(text: str, ts: float) -> None:
    """Persist one transcript, then prune to ``DB_MAX_ROWS`` (oldest-first)."""
    try:
        # closing() guarantees the handle is released — on Windows a lingering
        # open connection would block delete_file() (WinError 32). The inner
        # `conn` context manager commits/rolls back the transaction.
        with contextlib.closing(_connect()) as conn, conn:
            conn.execute("INSERT INTO transcripts (text, ts) VALUES (?, ?)", (text, ts))
            conn.execute(
                "DELETE FROM transcripts WHERE id NOT IN "
                "(SELECT id FROM transcripts ORDER BY id DESC LIMIT ?)",
                (DB_MAX_ROWS,),
            )
    except sqlite3.Error as e:
        log.warning("could not persist transcript: %s", e)


def load_recent(limit: int) -> list[tuple[str, float]]:
    """The most recent ``limit`` rows as ``(text, ts)``, oldest-first."""
    try:
        with contextlib.closing(_connect()) as conn:
            rows = conn.execute(
                "SELECT text, ts FROM transcripts ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [(t, ts) for t, ts in reversed(rows)]
    except sqlite3.Error as e:
        log.warning("could not read history db: %s", e)
        return []


def clear() -> None:
    """Delete all persisted rows (keeps the empty file + schema)."""
    try:
        with contextlib.closing(_connect()) as conn, conn:
            conn.execute("DELETE FROM transcripts")
    except sqlite3.Error as e:
        log.warning("could not clear history db: %s", e)


def delete_file() -> None:
    """Remove the database file entirely — called when persistence is turned
    off, so no transcript trace is left on disk."""
    try:
        db_path().unlink(missing_ok=True)
    except OSError as e:
        log.warning("could not delete history db: %s", e)
