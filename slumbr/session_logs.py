"""Session logs — temporary, per-session archive of dictation batches.

The live History tab holds the current *partial* batch (< ``history.MAX_ENTRIES``
entries). Each time it fills, those entries roll into a numbered **session log**
here, and the live view resets to fresh. Session logs are the fallback for
"I hit the cap, the list auto-cleared, and I didn't realise I needed that one."

They are TEMPORARY by design: a clean quit deletes the whole ``session/`` folder,
so they never accumulate on disk across runs. They DO survive a crash, though —
that's the point. A ``lock.json`` "running" marker is dropped at session start
and removed on clean shutdown; if the next launch still finds it, the previous
session crashed. Its batches + partial history are then offered for recovery
*and* dumped to a durable crash-log file under ``crash-logs/`` for digging.

This module owns no Qt and no history-read logic — it works on plain
``(text, ts)`` records and lets ``app.py`` orchestrate the lifecycle. It does
not import ``history`` at module load (avoids a cycle); the few places that
need ``HistoryEntry`` import it lazily.

Layout under %APPDATA%/Slumbr (``~/.slumbr`` off-Windows):
    session/lock.json            running marker {pid, started_at}
    session/batch-0001.jsonl     completed batch, one JSON record per line
    crash-logs/crash-<stamp>.txt durable, human-readable breadcrumb
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

MAX_CRASH_LOGS = 10  # keep only the most recent N crash breadcrumbs


def _base_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    return Path(appdata) / "Slumbr" if appdata else Path.home() / ".slumbr"


def _session_dir() -> Path:
    return _base_dir() / "session"


def _crash_dir() -> Path:
    return _base_dir() / "crash-logs"


def _lock_path() -> Path:
    return _session_dir() / "lock.json"


@dataclass(frozen=True)
class BatchMeta:
    """A completed session log on disk — enough to render its row without
    loading every transcript."""

    index: int          # 1-based; matches the "Log N" label
    count: int          # number of transcripts in the batch
    first_ts: float
    last_ts: float
    path: Path


# ---------------------------------------------------------- batch read/write


def _batch_path(index: int) -> Path:
    return _session_dir() / f"batch-{index:04d}.jsonl"


def _next_index() -> int:
    return len(list_batches()) + 1


def list_batches() -> list[BatchMeta]:
    """All completed session logs, oldest-first. Empty on any I/O error."""
    sdir = _session_dir()
    if not sdir.is_dir():
        return []
    metas: list[BatchMeta] = []
    for p in sorted(sdir.glob("batch-*.jsonl")):
        try:
            idx = int(p.stem.split("-")[1])
        except (IndexError, ValueError):
            continue
        rows = _read_records(p)
        if not rows:
            continue
        ts = [r["ts"] for r in rows]
        metas.append(
            BatchMeta(index=idx, count=len(rows), first_ts=min(ts), last_ts=max(ts), path=p)
        )
    metas.sort(key=lambda m: m.index)
    return metas


def _read_records(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        log.warning("could not read batch %s: %s", path, e)
        return []
    out: list[dict] = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            data = json.loads(ln)
            out.append({"text": str(data.get("text", "")), "ts": float(data.get("ts", 0.0))})
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
    return out


def roll_batch(entries: list) -> int | None:
    """Write ``entries`` (objects with ``.text`` + ``.ts``) as the next numbered
    session log. Returns the new batch index, or ``None`` if nothing was written.
    """
    rows = [
        {"text": e.text, "ts": float(e.ts)}
        for e in entries
        if getattr(e, "text", "").strip()
    ]
    if not rows:
        return None
    idx = _next_index()
    path = _batch_path(idx)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".jsonl.tmp")
    try:
        tmp.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except OSError as e:
        log.warning("could not write session batch %s: %s", path, e)
        return None
    return idx


def load_batch(index: int) -> list:
    """Return a batch's transcripts as ``HistoryEntry`` objects (newest concerns
    handled by the caller). Lazy import keeps this module free of a cycle."""
    from .history import HistoryEntry

    return [HistoryEntry(text=r["text"], ts=r["ts"]) for r in _read_records(_batch_path(index))]


# ---------------------------------------------------------- lifecycle


def previous_session_crashed() -> bool:
    """True if a running-marker from a prior launch is still present — i.e. the
    last session didn't shut down cleanly."""
    return _lock_path().is_file()


def begin() -> None:
    """Drop the running marker for this session. Call once at startup, AFTER
    crash detection."""
    sdir = _session_dir()
    sdir.mkdir(parents=True, exist_ok=True)
    try:
        _lock_path().write_text(
            json.dumps({"pid": os.getpid(), "started_at": time.time()}),
            encoding="utf-8",
        )
    except OSError as e:
        log.warning("could not write session lock: %s", e)


def reset() -> None:
    """Delete every batch + the lock (fresh slate). Used on a clean launch and
    on Discard. Best-effort; leaves ``crash-logs/`` untouched."""
    sdir = _session_dir()
    if not sdir.is_dir():
        return
    for p in list(sdir.glob("batch-*.jsonl")) + list(sdir.glob("*.tmp")):
        try:
            p.unlink()
        except OSError:
            pass
    try:
        _lock_path().unlink(missing_ok=True)
    except OSError:
        pass


def end() -> None:
    """Clean shutdown: wipe the session folder so nothing carries over. Same
    effect as ``reset`` but reads intentionally at the call site."""
    reset()


# ---------------------------------------------------------- crash breadcrumb


def write_crash_log(entries: list) -> Path | None:
    """Dump a recovered session to a durable, human-readable crash log and prune
    to the newest ``MAX_CRASH_LOGS``. ``entries`` are objects with ``.text`` +
    ``.ts``. Returns the path written, or ``None``."""
    rows = [e for e in entries if getattr(e, "text", "").strip()]
    if not rows:
        return None
    cdir = _crash_dir()
    cdir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    path = cdir / f"crash-{stamp}.txt"
    n = 1
    while path.exists():  # never clobber a prior crash within the same second
        path = cdir / f"crash-{stamp}-{n}.txt"
        n += 1
    from datetime import datetime

    lines = [
        f"Slumbr crash-recovered transcripts — {len(rows)} entries",
        f"Recovered {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "These are the dictations from a session that didn't close cleanly.",
        "-" * 60,
        "",
    ]
    for e in rows:
        when = datetime.fromtimestamp(float(e.ts)).strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"[{when}] {e.text}")
    try:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as e:  # noqa: F841
        log.warning("could not write crash log %s", path)
        return None
    _prune_crash_logs()
    return path


def write_crash_traceback(tb: str) -> Path | None:
    """Drop a Python traceback as its own crash breadcrumb (auto-detect path:
    an uncaught exception). Shares the ``crash-*.txt`` namespace so it's pruned
    with the transcript crash logs."""
    cdir = _crash_dir()
    cdir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    path = cdir / f"crash-traceback-{stamp}.txt"
    n = 1
    while path.exists():
        path = cdir / f"crash-traceback-{stamp}-{n}.txt"
        n += 1
    try:
        path.write_text(
            f"Slumbr crashed {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n{tb}\n",
            encoding="utf-8",
        )
    except OSError:
        return None
    _prune_crash_logs()
    return path


def _prune_crash_logs() -> None:
    cdir = _crash_dir()
    logs = sorted(cdir.glob("crash-*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in logs[MAX_CRASH_LOGS:]:
        try:
            old.unlink()
        except OSError:
            pass
