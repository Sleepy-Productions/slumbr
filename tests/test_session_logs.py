"""Single-instance guard (slumbr/session_logs.py).

Slumbr keeps no transcript logs on disk anymore; ``session_logs`` is now just
the single-instance lock + reopen marker. All paths are redirected under a tmp
APPDATA so tests never touch the real %APPDATA%/Slumbr folder.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from slumbr import session_logs


@pytest.fixture
def appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path


def _write_lock(appdata, pid: int) -> None:
    sdir = appdata / "Slumbr" / "session"
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "lock.json").write_text(json.dumps({"pid": pid, "started_at": 0.0}), encoding="utf-8")


# ---------------------------------------------------------- single-instance


def test_no_lock_is_not_another_instance(appdata):
    assert session_logs.another_instance_running() is False


def test_own_live_lock_is_us_not_another(appdata):
    # begin() writes a lock owned by THIS (alive) process — that's us, not
    # "another" instance. end() removes it.
    session_logs.begin()
    assert session_logs.another_instance_running() is False
    session_logs.end()
    assert session_logs._lock_path().exists() is False


def test_orphaned_lock_does_not_block_relaunch(appdata):
    # A lock owned by a dead PID must NOT read as "another instance" — the next
    # launch proceeds instead of falsely surfacing+exiting.
    _write_lock(appdata, 0x7FFFFFFE)  # a PID that is not running
    assert session_logs.another_instance_running() is False


def test_live_other_instance_is_detected(appdata):
    # A lock owned by another ALIVE process (the parent) = a real second copy.
    _write_lock(appdata, os.getppid())
    assert session_logs.another_instance_running() is True


def test_show_request_round_trip(appdata):
    assert session_logs.consume_show_request() is False
    session_logs.request_show()
    assert session_logs.consume_show_request() is True
    assert session_logs.consume_show_request() is False  # cleared


# ----- PID-liveness: a force-killed process must read as DEAD even while a
# handle to it lingers (Task Manager / parent shell), or the next launch from
# the pinned shortcut misfires as a phantom "already running".


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process-handle semantics")
def test_pid_alive_true_for_self():
    assert session_logs._pid_alive(os.getpid()) is True


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process-handle semantics")
def test_killed_process_reads_dead_even_with_handle_held():
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
    # create_time it would have recorded) must NOT count as "another instance".
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


# ----- a corrupt lock.json must never crash the startup guard (it's read on
# every launch). Valid-JSON-but-not-an-object used to crash _lock_owner on .get.


@pytest.mark.parametrize(
    "content",
    [
        "{ not json",
        "",
        "   ",
        "[1,2,3]",
        "42",
        "null",
        '"hello"',
        '{"started_at": 0}',  # no pid
        '{"pid": "abc"}',
        '{"pid": null}',
        '{"pid": [1, 2]}',
        '{"pid": 1.5}',
        '{"pid": 4242, "create_time": "soon"}',  # garbage create_time
    ],
)
def test_corrupt_lock_never_crashes_startup_guard(appdata, content):
    sdir = appdata / "Slumbr" / "session"
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "lock.json").write_text(content, encoding="utf-8")
    assert isinstance(session_logs.another_instance_running(), bool)
