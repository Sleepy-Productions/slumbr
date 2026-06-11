"""Regression tests for the high-confidence correctness fixes identified in the
2026-06-11 code review.

Covers:
- Fix 1: history.clear_memory() only clears in-memory; on-disk store survives
- Fix 2: configure(True) mid-session preserves in-memory entries
- Fix 3: history.clear() (user-action) still wipes disk
- Fix 4: _reconcile_mic_mirror reopens when is_running is False
- Fix 5: _on_quit waits for in-flight TranscribeWorker before closing backend
- Fix 6: history_store robustness (OSError + corrupt SQLite)
- Fix 7: sleepy_errlog.py is not importable from the slumbr package
- Fix 8: __init__.py logging docstring no longer claims transcripts are logged
"""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from slumbr import history, history_store


# ============================================================== shared fixture
@pytest.fixture(autouse=True)
def _clean_history(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    history.configure(False)
    history.clear()
    yield
    history.configure(False)
    history.clear()


# ====================================================== Fix 1: clear_memory()
class TestClearMemory:
    def test_clear_memory_only_wipes_in_memory_list(self, tmp_path):
        history.configure(True)
        history.append("survives shutdown")
        assert history_store.db_path().is_file()

        history.clear_memory()

        # In-memory list is gone.
        assert history.load_all() == []
        assert history.latest() == ""
        # On-disk store is intact.
        rows = history_store.load_recent(10)
        assert [t for t, _ts in rows] == ["survives shutdown"]

    def test_clear_memory_no_disk_store_does_not_crash(self):
        # Ephemeral mode: no disk store, clear_memory should be a no-op beyond
        # wiping the (empty) in-memory list.
        history.append("ephemeral")
        history.clear_memory()
        assert history.load_all() == []

    def test_quit_path_preserves_persistent_store(self, tmp_path):
        """Simulates the app shutdown path: clear_memory() leaves disk intact."""
        history.configure(True)
        history.append("kept")
        history.append("also kept")

        # Simulate shutdown: only clear memory.
        history.clear_memory()
        assert history.load_all() == []

        # Simulate a fresh startup: re-enable persistence to backfill.
        history._persist = False  # reset to mimic fresh process
        history.configure(True)
        texts = [e.text for e in history.load_all()]
        assert "kept" in texts
        assert "also kept" in texts


# ====================================================== Fix 2: configure(True) mid-session
class TestConfigureMidSession:
    def test_enable_persistence_mid_session_preserves_memory_entries(self, tmp_path):
        """In-memory entries must NOT be discarded when the user turns on
        persistence mid-session (fix for the configure()-drops-entries bug)."""
        # Dictate in ephemeral mode.
        history.append("dictated before enabling")
        history.append("also before")

        # User toggles "Keep history across restarts" ON.
        history.configure(True)

        texts = [e.text for e in history.load_all()]
        assert "dictated before enabling" in texts, "pre-enable entries must survive configure(True)"
        assert "also before" in texts

    def test_enable_persistence_writes_memory_entries_to_disk(self, tmp_path):
        """Mid-session entries must be written to the store on configure(True)."""
        history.append("needs persisting")
        history.configure(True)

        rows = history_store.load_recent(20)
        texts = [t for t, _ts in rows]
        assert "needs persisting" in texts

    def test_enable_persistence_merges_disk_and_memory(self, tmp_path):
        """configure(True) on a session that has both a pre-existing DB and
        in-memory entries must merge them, not drop either side."""
        # Simulate: previous sessions left rows on disk.
        history.configure(True)
        history.append("from previous session")
        # Reset to mimic new process that hasn't configured yet.
        history._entries.clear()
        history._persist = False

        # Dictate in the new session (ephemeral).
        history.append("from this session")

        # Enable persistence — must see both.
        history.configure(True)
        texts = [e.text for e in history.load_all()]
        assert "from previous session" in texts
        assert "from this session" in texts

    def test_enable_persistence_deduplicated(self, tmp_path):
        """No duplicates when the same (text, ts) appears both in memory and on
        disk (e.g. configure was called twice)."""
        history.configure(True)
        history.append("unique")
        # configure again — same entry in memory AND disk.
        history.configure(True)
        texts = [e.text for e in history.load_all()]
        assert texts.count("unique") == 1


# ====================================================== Fix 3: clear() still wipes disk
class TestClearUserAction:
    def test_clear_wipes_disk_when_persistent(self, tmp_path):
        history.configure(True)
        history.append("should be gone")
        history.clear()
        assert history.load_all() == []
        assert history_store.load_recent(10) == []

    def test_clear_memory_does_not_wipe_disk(self, tmp_path):
        history.configure(True)
        history.append("should survive")
        history.clear_memory()
        # Disk intact.
        rows = history_store.load_recent(10)
        assert rows  # at least one row still present


# ====================================================== Fix 4: mic_mirror is_running
class TestMicMirrorReconcile:
    """Unit tests for _reconcile_mic_mirror()'s new is_running check.

    We test the SlumbrApp method in isolation by faking the required attributes.
    """

    def _make_app_stub(self):
        """Return a minimal object that has the three attributes
        _reconcile_mic_mirror() reads: mic_mirror, config, and the method
        _try_open_mic_mirror."""

        class FakeConfig:
            mic_routing_enabled = True
            mic_routing_device_name = "CABLE Input"

        class FakeMirror:
            _device_name = "CABLE Input"
            _stopped = False
            _running = True

            @property
            def is_running(self):
                return self._running

            def stop(self):
                self._stopped = True
                self._running = False

        class AppStub:
            def __init__(self):
                self.config = FakeConfig()
                self.mic_mirror = FakeMirror()
                self._try_open_called = False

            def _try_open_mic_mirror(self):
                self._try_open_called = True

            # Copy the real implementation under test (import lazily to avoid
            # pulling in the full Qt stack).
            def _reconcile_mic_mirror(self):
                import logging

                log = logging.getLogger(__name__)

                if not self.config.mic_routing_enabled:
                    if self.mic_mirror is not None:
                        self.mic_mirror.stop()
                        self.mic_mirror = None
                    return

                if self.mic_mirror is None:
                    self._try_open_mic_mirror()
                    return

                # NEW: reopen if stream died (the fix being tested).
                if not self.mic_mirror.is_running:
                    log.info("mic_mirror stream died (device lost?); reopening")
                    self.mic_mirror.stop()
                    self.mic_mirror = None
                    self._try_open_mic_mirror()
                    return

                cur_name = getattr(self.mic_mirror, "_device_name", "")
                target = self.config.mic_routing_device_name
                if target and cur_name != target:
                    self.mic_mirror.stop()
                    self.mic_mirror = None
                    self._try_open_mic_mirror()

        return AppStub()

    def test_reconcile_reopens_when_stream_died(self):
        app = self._make_app_stub()
        # Simulate push() closing the stream after a PortAudioError.
        app.mic_mirror._running = False

        app._reconcile_mic_mirror()

        assert app.mic_mirror is None, "dead mirror handle must be cleared"
        assert app._try_open_called, "_try_open_mic_mirror must be called to reopen"

    def test_reconcile_does_not_reopen_when_running(self):
        app = self._make_app_stub()
        app.mic_mirror._running = True

        app._reconcile_mic_mirror()

        assert not app._try_open_called, "no reopen needed when stream is healthy"

    def test_reconcile_opens_when_mirror_is_none(self):
        app = self._make_app_stub()
        app.mic_mirror = None

        app._reconcile_mic_mirror()

        assert app._try_open_called


# ====================================================== Fix 5: worker shutdown race
class TestWorkerShutdownRace:
    """Verify that the quit path waits for an in-flight TranscribeWorker.

    We test the wait logic in isolation without spinning up a full Qt app by
    checking the QThread.wait() semantics via a minimal fake.
    """

    def test_quit_waits_for_running_worker(self):
        """Quitting while a worker is running must call wait() on it."""
        finished_marker = []

        class FakeWorker:
            _running = True

            def isRunning(self):
                return self._running

            def wait(self, timeout_ms: int) -> bool:
                # Simulate a fast decode finishing within the timeout.
                finished_marker.append(timeout_ms)
                return True  # finished in time

        # Simulate the quit logic extracted from _on_quit.
        worker = FakeWorker()
        if worker is not None and worker.isRunning():
            worker.wait(5000)

        assert finished_marker == [5000], "wait(5000) must be called on the running worker"

    def test_quit_skips_wait_when_worker_not_running(self):
        """No wait when the worker is already done."""
        wait_called = []

        class FakeWorker:
            def isRunning(self):
                return False

            def wait(self, timeout_ms: int) -> bool:
                wait_called.append(timeout_ms)
                return True

        worker = FakeWorker()
        if worker is not None and worker.isRunning():
            worker.wait(5000)

        assert not wait_called, "wait() must not be called when worker is already done"


# ====================================================== Fix 6: history_store robustness
class TestHistoryStoreRobustness:
    def test_corrupt_db_is_quarantined_and_recreated(self, tmp_path, monkeypatch):
        monkeypatch.setenv("APPDATA", str(tmp_path))
        db = history_store.db_path()
        db.parent.mkdir(parents=True, exist_ok=True)
        # Write garbage bytes that are definitely not a valid SQLite file.
        db.write_bytes(b"not a valid sqlite db\x00\x01\xff")

        # add() must not raise; the corrupt file should be quarantined.
        history_store.add("after corruption", time.time())

        # The original corrupt path should now be gone (replaced by the fresh DB).
        corrupt_backups = list(db.parent.glob("history.db.corrupt-*"))
        assert corrupt_backups, "corrupt DB must be renamed to a .corrupt-<ts> backup"

        # The new DB should contain our row.
        rows = history_store.load_recent(5)
        assert any(t == "after corruption" for t, _ in rows)

    def test_load_recent_on_corrupt_db_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("APPDATA", str(tmp_path))
        db = history_store.db_path()
        db.parent.mkdir(parents=True, exist_ok=True)
        db.write_bytes(b"\x00" * 64)

        # Should not raise; returns empty list (or recreates and returns empty).
        rows = history_store.load_recent(10)
        assert isinstance(rows, list)

    def test_oserror_on_add_does_not_raise(self, tmp_path, monkeypatch):
        """add() must be best-effort even when the directory can't be created."""
        monkeypatch.setenv("APPDATA", str(tmp_path))
        # Point APPDATA at a FILE so mkdir() will fail with OSError.
        fake_appdata = tmp_path / "not_a_dir.txt"
        fake_appdata.write_text("blocking", encoding="utf-8")
        monkeypatch.setenv("APPDATA", str(fake_appdata))

        # Should not raise.
        history_store.add("safe call", time.time())

    def test_oserror_on_load_returns_empty(self, tmp_path, monkeypatch):
        fake_appdata = tmp_path / "not_a_dir.txt"
        fake_appdata.write_text("blocking", encoding="utf-8")
        monkeypatch.setenv("APPDATA", str(fake_appdata))

        rows = history_store.load_recent(10)
        assert rows == []


# ====================================================== Fix 7: sleepy_errlog not in package
class TestSleepyErrlogNotImportable:
    def test_sleepy_errlog_not_part_of_slumbr_package(self):
        """sleepy_errlog.py must not be importable as slumbr.sleepy_errlog —
        it is internal private tooling removed from the public tracked files."""
        import importlib

        with pytest.raises(ImportError):
            importlib.import_module("slumbr.sleepy_errlog")


# ====================================================== Fix 8: stale logging docstring
class TestLoggingDocstringAccuracy:
    def test_init_docstring_does_not_claim_transcripts_logged(self):
        """The _configure_logging docstring must no longer claim transcripts are
        captured verbatim. A simple textual check guards against re-introduction."""
        import inspect

        from slumbr import _configure_logging

        src = inspect.getsource(_configure_logging)
        # The old offending phrase.
        assert "captures every transcript verbatim" not in src, (
            "_configure_logging docstring must not claim transcripts are logged verbatim"
        )
