"""Session-log store + history batch-roll behavior.

All paths are redirected under a tmp APPDATA so tests never touch the real
%APPDATA%/Slumbr folder.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest

from slumbr import history, session_logs
from slumbr.history import HistoryEntry


@pytest.fixture
def appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    # history.HISTORY_PATH is resolved at import time — repoint it under tmp.
    monkeypatch.setattr(history, "HISTORY_PATH", tmp_path / "Slumbr" / "history.jsonl")
    return tmp_path


def _e(text: str, ts: float | None = None) -> HistoryEntry:
    return HistoryEntry(text=text, ts=ts if ts is not None else time.time())


# ---------------------------------------------------------- batches


def test_roll_batch_round_trip(appdata):
    idx = session_logs.roll_batch([_e("alpha"), _e("beta")])
    assert idx == 1
    metas = session_logs.list_batches()
    assert len(metas) == 1
    assert metas[0].index == 1
    assert metas[0].count == 2
    assert [e.text for e in session_logs.load_batch(1)] == ["alpha", "beta"]


def test_next_index_increments(appdata):
    assert session_logs.roll_batch([_e("x")]) == 1
    assert session_logs.roll_batch([_e("y")]) == 2
    assert [m.index for m in session_logs.list_batches()] == [1, 2]


def test_roll_batch_empty_or_whitespace_returns_none(appdata):
    assert session_logs.roll_batch([]) is None
    assert session_logs.roll_batch([_e("   ")]) is None
    assert session_logs.list_batches() == []


# ---------------------------------------------------------- history roll


def test_history_fills_to_cap_without_rolling(appdata):
    for i in range(history.MAX_ENTRIES):
        history.append(f"t{i}")
    assert len(history.load_all()) == history.MAX_ENTRIES
    assert session_logs.list_batches() == []


def test_history_rolls_and_resets_on_overflow(appdata):
    for i in range(history.MAX_ENTRIES):
        history.append(f"t{i}")
    history.append("overflow")  # the cap+1 th
    live = history.load_all()
    assert len(live) == 1
    assert live[0].text == "overflow"
    batches = session_logs.list_batches()
    assert len(batches) == 1
    assert batches[0].count == history.MAX_ENTRIES
    rolled = session_logs.load_batch(1)
    assert [e.text for e in rolled] == [f"t{i}" for i in range(history.MAX_ENTRIES)]


# ---------------------------------------------------------- lifecycle


def _write_lock(appdata, pid: int) -> None:
    sdir = appdata / "Slumbr" / "session"
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "lock.json").write_text(json.dumps({"pid": pid, "started_at": 0.0}), encoding="utf-8")


def test_no_lock_is_clean(appdata):
    assert session_logs.previous_session_crashed() is False
    assert session_logs.another_instance_running() is False


def test_own_live_lock_is_not_a_crash(appdata):
    # begin() writes a lock owned by THIS (alive) process — not a crash, and not
    # "another" instance (it's us). end() removes it.
    session_logs.begin()
    assert session_logs.previous_session_crashed() is False
    assert session_logs.another_instance_running() is False
    session_logs.end()
    assert session_logs.previous_session_crashed() is False


def test_orphaned_lock_is_a_crash(appdata):
    # A lock owned by a dead PID = a genuine unclean exit.
    _write_lock(appdata, 0x7FFFFFFE)  # a PID that is not running
    assert session_logs.previous_session_crashed() is True
    assert session_logs.another_instance_running() is False


def test_live_other_instance_is_not_a_crash(appdata):
    # A lock owned by another ALIVE process (the parent) = concurrent launch.
    _write_lock(appdata, os.getppid())
    assert session_logs.another_instance_running() is True
    assert session_logs.previous_session_crashed() is False


# ----- PID-liveness: a force-killed process must read as DEAD even while a
# handle to it lingers (Task Manager / parent shell), or the next launch from
# the pinned shortcut misfires as a phantom "already running". Regression for
# the zombie-handle bug: OpenProcess still succeeds on a terminated process, so
# _pid_alive must additionally check the exit code (GetExitCodeProcess != 259).

@pytest.mark.skipif(sys.platform != "win32", reason="Windows process-handle semantics")
def test_pid_alive_true_for_self():
    assert session_logs._pid_alive(os.getpid()) is True


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process-handle semantics")
def test_killed_process_reads_dead_even_with_handle_held():
    # Keep the Popen object alive -> Windows keeps a handle open -> the dead
    # PID stays OpenProcess-able (a zombie). The hardened check must still say
    # "not alive".
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert session_logs._pid_alive(p.pid) is True  # sanity: alive while running
        p.kill()
        p.wait()
        assert session_logs._pid_alive(p.pid) is False  # the fix
    finally:
        if p.poll() is None:
            p.kill()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process-handle semantics")
def test_killed_instance_does_not_block_relaunch(appdata):
    # End-to-end: a lock left by a force-killed instance (real PID + the
    # create_time it would have recorded) must NOT count as "another instance",
    # so the pinned shortcut relaunches cleanly. It IS a crash (owner gone).
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    ct = session_logs._pid_create_time(p.pid)
    p.kill()
    p.wait()
    sdir = appdata / "Slumbr" / "session"
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "lock.json").write_text(
        json.dumps({"pid": p.pid, "started_at": 0.0, "create_time": ct}),
        encoding="utf-8",
    )
    assert session_logs.another_instance_running() is False
    assert session_logs.previous_session_crashed() is True


# ----- a corrupt lock.json must never crash the startup guards (it's read on
# every launch). Valid-JSON-but-not-an-object used to crash _lock_owner on .get.

@pytest.mark.parametrize("content", [
    "{ not json", "", "   ", "[1,2,3]", "42", "null", '"hello"',
    '{"started_at": 0}',                       # no pid
    '{"pid": "abc"}', '{"pid": null}', '{"pid": [1, 2]}', '{"pid": 1.5}',
    '{"pid": 4242, "create_time": "soon"}',    # garbage create_time
])
def test_corrupt_lock_never_crashes_startup_guards(appdata, content):
    sdir = appdata / "Slumbr" / "session"
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "lock.json").write_text(content, encoding="utf-8")
    assert isinstance(session_logs.previous_session_crashed(), bool)
    assert isinstance(session_logs.another_instance_running(), bool)


# ---------------------------------------------------------- crash log


def test_crash_log_written(appdata):
    p = session_logs.write_crash_log([_e("hello"), _e("world")])
    assert p is not None and p.is_file()
    body = p.read_text(encoding="utf-8")
    assert "hello" in body and "world" in body


def test_crash_log_pruned_to_max(appdata):
    for i in range(session_logs.MAX_CRASH_LOGS + 4):
        session_logs.write_crash_log([_e(f"c{i}")])
    cdir = appdata / "Slumbr" / "crash-logs"
    assert len(list(cdir.glob("crash-*.txt"))) <= session_logs.MAX_CRASH_LOGS
