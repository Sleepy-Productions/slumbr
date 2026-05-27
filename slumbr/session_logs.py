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
    session/lock.json            running marker {pid, started_at, create_time}
    session/batch-0001.jsonl     completed batch, one JSON record per line
    crash-logs/crash-<stamp>.txt durable, human-readable breadcrumb
"""

from __future__ import annotations

import json
import logging
import os
import sys
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


def session_dir() -> Path:
    """Public path to this session's working dir — the running instance watches
    it for the reopen ``show.request`` marker (see ``request_show``)."""
    return _session_dir()


def _show_request_path() -> Path:
    return _session_dir() / "show.request"


def request_show() -> None:
    """Ask an already-running Slumbr to surface its Settings window. A second
    launch — e.g. clicking the pinned taskbar icon while Slumbr sits in the tray
    with no window open — drops this marker and exits; the running instance's
    directory watcher picks it up and opens Settings. This is what makes "reopen
    from the taskbar" work even when there's no window for the OS to re-activate.
    """
    try:
        p = _show_request_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(time.time()), encoding="utf-8")
    except OSError as e:
        log.warning("could not write show request: %s", e)


def consume_show_request() -> bool:
    """True (and clears the marker) if a second launch asked us to surface the
    window. Best-effort: a failed unlink still reports True so the click isn't
    silently dropped."""
    if not _show_request_path().is_file():
        return False
    try:
        _show_request_path().unlink()
    except OSError:
        pass
    return True


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


def _pid_alive(pid: int) -> bool:
    """Is a process with this PID currently *running*? (Windows; best-effort.)

    OpenProcess succeeding is NOT enough. A force-killed / End-Task'd PID stays
    queryable for as long as any handle to it lingers (Task Manager, a parent
    shell) — the process is terminated but its kernel object hasn't been reaped.
    Such a zombie answers OpenProcess AND GetProcessTimes with its original
    values, so a liveness check that trusts OpenProcess reads it as "alive" and
    the next launch from the pinned shortcut misfires as a phantom "already
    running" (the create-time guard can't catch this — same process, same
    create time). GetExitCodeProcess returns STILL_ACTIVE (259) ONLY while the
    process is genuinely running, so it tells live apart from terminated."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        k = ctypes.windll.kernel32
        h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return False
        try:
            code = wintypes.DWORD()
            if not k.GetExitCodeProcess(h, ctypes.byref(code)):
                return True  # can't read state but the handle opened — assume live
            return code.value == STILL_ACTIVE
        finally:
            k.CloseHandle(h)
    except Exception:  # noqa: BLE001
        return False


def _pid_create_time(pid: int) -> int | None:
    """A process's creation time as a Windows FILETIME (100ns ticks), or None.

    This is what makes the single-instance lock PID-reuse-proof. After an End
    Task / kill, the dead instance's PID is freed and Windows can hand it to an
    unrelated process. A liveness-only check (``_pid_alive``) would then see the
    recycled PID as "alive" and wrongly conclude Slumbr is still running, so the
    next launch from the pinned shortcut silently no-ops. The recycled process
    has a *different* creation time, so matching it against the time we recorded
    at lock-write distinguishes "still us" from "PID reused"."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        k = ctypes.windll.kernel32
        h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return None
        try:
            creation, exit_t, kernel_t, user_t = (wintypes.FILETIME() for _ in range(4))
            ok = k.GetProcessTimes(
                h, ctypes.byref(creation), ctypes.byref(exit_t),
                ctypes.byref(kernel_t), ctypes.byref(user_t),
            )
            if not ok:
                return None
            return (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        finally:
            k.CloseHandle(h)
    except Exception:  # noqa: BLE001
        return None


def _lock_owner() -> tuple[int | None, int | None]:
    """``(pid, create_time)`` recorded in the lock — ``create_time`` is ``None``
    for a legacy lock written before PID-reuse hardening."""
    try:
        data = json.loads(_lock_path().read_text(encoding="utf-8"))
        ct = data.get("create_time")
        return int(data["pid"]), (int(ct) if ct is not None else None)
    except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError):
        return None, None


def _owner_still_running() -> bool:
    """True iff the lock's owner is alive AND is the same process that wrote it
    (PID *and* creation time match). A recycled PID is alive but has a different
    creation time → treated as gone, so a stale lock never blocks relaunch."""
    pid, ct = _lock_owner()
    if pid is None or not _pid_alive(pid):
        return False
    if ct is None:
        return True  # legacy lock without a creation time → liveness only
    live = _pid_create_time(pid)
    return live is not None and live == ct


def another_instance_running() -> bool:
    """True if the session lock is held by a DIFFERENT, still-running Slumbr —
    i.e. another instance is genuinely alive. Distinct from a crash (owner gone)
    and from a recycled PID (owner gone, its PID since reused). Single-instance
    guard."""
    if not _lock_path().is_file():
        return False
    pid, _ = _lock_owner()
    if pid is None or pid == os.getpid():
        return False
    return _owner_still_running()


def focus_existing() -> None:
    """Best-effort: surface a running Slumbr's Settings window, so a second
    launch brings the app forward instead of silently doing nothing."""
    if sys.platform != "win32":
        return
    try:
        import win32con
        import win32gui

        def _cb(hwnd, _):
            t = win32gui.GetWindowText(hwnd)
            if win32gui.IsWindowVisible(hwnd) and "Slumbr" in t and "Settings" in t:
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                win32gui.SetForegroundWindow(hwnd)

        win32gui.EnumWindows(_cb, None)
    except Exception:  # noqa: BLE001
        pass


def previous_session_crashed() -> bool:
    """True only if a session lock was left by a process that is NO LONGER
    running — a genuine unclean exit. A lock held by a live instance is a
    concurrent launch (handled by the single-instance guard), NOT a crash, so
    it must not trigger the recovery prompt. A recycled PID counts as "owner
    gone" (the original process is no longer running), so an End-Task'd session
    is correctly recoverable rather than misread as a still-live instance."""
    if not _lock_path().is_file():
        return False
    return not _owner_still_running()


def begin() -> None:
    """Drop the running marker for this session. Call once at startup, AFTER
    crash detection."""
    sdir = _session_dir()
    sdir.mkdir(parents=True, exist_ok=True)
    try:
        _lock_path().write_text(
            json.dumps({
                "pid": os.getpid(),
                "started_at": time.time(),
                # Creation time of THIS process — the anchor that lets the next
                # launch tell a live instance from a recycled PID (see
                # _owner_still_running). Best-effort; None on non-Windows.
                "create_time": _pid_create_time(os.getpid()),
            }),
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
        _show_request_path().unlink(missing_ok=True)
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
