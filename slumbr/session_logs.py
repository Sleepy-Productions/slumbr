"""Single-instance guard + reopen-request marker.

Slumbr keeps no transcript logs on disk (History is in-memory and ephemeral —
see ``history.py``). The only thing tracked under ``%APPDATA%/Slumbr/session/``
is a tiny ``lock.json`` "running" marker, used purely for single-instance
detection:

- ``begin()`` drops the marker (pid + creation time) at startup.
- ``another_instance_running()`` tells a second launch that a *live* instance
  already owns the lock, so it surfaces that one and exits instead of starting a
  duplicate (which would double the Caps Lock hook + leave a stray taskbar
  button). The check is PID-reuse- and zombie-proof (creation-time match +
  ``GetExitCodeProcess`` liveness), so a force-killed instance never wrongly
  blocks the next launch.
- ``end()`` removes the marker on a clean shutdown.
- ``request_show()`` / ``consume_show_request()`` let a second launch ask the
  running instance to surface its Settings window (the pinned-icon-click case
  where there's no window for the OS to re-activate).

No batches, no crash logs, no recovery — closing Slumbr leaves nothing behind
but the (removed-on-clean-exit) lock.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

log = logging.getLogger(__name__)


def _base_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    return Path(appdata) / "Slumbr" if appdata else Path.home() / ".slumbr"


def _session_dir() -> Path:
    return _base_dir() / "session"


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
    directory watcher picks it up and opens Settings."""
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


# ---------------------------------------------------------- single-instance


def _pid_alive(pid: int) -> bool:
    """Is a process with this PID currently *running*? (Windows; best-effort.)

    OpenProcess succeeding is NOT enough. A force-killed / End-Task'd PID stays
    queryable for as long as any handle to it lingers (Task Manager, a parent
    shell) — the process is terminated but its kernel object hasn't been reaped.
    GetExitCodeProcess returns STILL_ACTIVE (259) ONLY while the process is
    genuinely running, so it tells live apart from terminated."""
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
    unrelated process; the recycled process has a *different* creation time, so
    matching it against the time recorded at lock-write distinguishes "still us"
    from "PID reused"."""
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
        if not isinstance(data, dict):
            return None, None
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
    i.e. another instance is genuinely alive. Single-instance guard."""
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


# ---------------------------------------------------------- lifecycle


def begin() -> None:
    """Drop the running marker for this session. Call once at startup."""
    sdir = _session_dir()
    sdir.mkdir(parents=True, exist_ok=True)
    try:
        _lock_path().write_text(
            json.dumps({
                "pid": os.getpid(),
                "started_at": time.time(),
                # Creation time of THIS process — lets the next launch tell a
                # live instance from a recycled PID (see _owner_still_running).
                "create_time": _pid_create_time(os.getpid()),
            }),
            encoding="utf-8",
        )
    except OSError as e:
        log.warning("could not write session lock: %s", e)


def reset() -> None:
    """Remove the lock + reopen marker (fresh slate)."""
    try:
        _lock_path().unlink(missing_ok=True)
        _show_request_path().unlink(missing_ok=True)
    except OSError:
        pass


def end() -> None:
    """Clean shutdown: drop the running marker so the session leaves nothing
    behind. Reads intentionally at the call site."""
    reset()
