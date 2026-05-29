"""Transcript history (slumbr/history.py).

Ephemeral by default: history is a rolling in-memory window of MAX_ENTRIES —
past the cap the OLDEST entry is dropped (the list is never wiped). Nothing is
written to disk unless the user opts into persistence (configure(persist=True)),
which mirrors transcripts into a SQLite store and survives restarts.
"""

from __future__ import annotations

import pytest

from slumbr import history, history_store


@pytest.fixture(autouse=True)
def _clean_history(tmp_path, monkeypatch):
    # Isolate every test: ephemeral default + a throwaway APPDATA so the disk
    # store (when a test opts in) never touches the real profile.
    monkeypatch.setenv("APPDATA", str(tmp_path))
    history.configure(False)
    history.clear()
    yield
    history.configure(False)
    history.clear()


# --------------------------------------------------------------- in-memory


def test_cap_is_reasonable():
    assert history.MAX_ENTRIES >= 50


def test_appends_oldest_first_and_latest():
    history.append("a")
    history.append("b")
    assert [e.text for e in history.load_all()] == ["a", "b"]
    assert history.latest() == "b"


def test_fills_to_cap():
    for i in range(history.MAX_ENTRIES):
        history.append(f"t{i}")
    assert len(history.load_all()) == history.MAX_ENTRIES


def test_rolling_window_drops_oldest_not_all():
    # Fill to the cap, then push one more: the list stays at the cap, the OLDEST
    # entry is gone, and everything else (incl. the newcomer) survives. This is
    # the whole point — it must NOT wipe the list.
    for i in range(history.MAX_ENTRIES):
        history.append(f"t{i}")
    history.append("newest")
    live = history.load_all()
    assert len(live) == history.MAX_ENTRIES  # not 1 — no wipe
    assert live[0].text == "t1"  # t0 dropped
    assert live[-1].text == "newest"
    assert history.latest() == "newest"


def test_clear_empties():
    history.append("x")
    history.clear()
    assert history.load_all() == []
    assert history.latest() == ""


def test_whitespace_or_empty_ignored():
    history.append("   ")
    history.append("")
    assert history.load_all() == []


def test_nothing_written_to_disk_when_ephemeral(tmp_path, monkeypatch):
    # Default (no persistence): appending must not create ANY file under APPDATA.
    monkeypatch.setenv("APPDATA", str(tmp_path))
    history.configure(False)
    history.append("a private thought")
    assert list(tmp_path.rglob("*")) == []  # nothing persisted anywhere


# --------------------------------------------------------------- persistence


def test_persist_writes_to_disk():
    history.configure(True)
    history.append("kept across restarts")
    assert history_store.db_path().is_file()
    assert history_store.load_recent(10) and history_store.load_recent(10)[-1][0] == (
        "kept across restarts"
    )


def test_persist_backfills_on_enable():
    # Write some rows with persistence on, then simulate a fresh process: memory
    # empty + the persist flag reset (as a new launch starts), but the DB kept.
    # configure(True) should backfill the live view from disk.
    history.configure(True)
    history.append("one")
    history.append("two")
    history._entries.clear()
    history._persist = False
    history.configure(True)
    assert [e.text for e in history.load_all()] == ["one", "two"]


def test_disabling_persistence_deletes_the_file():
    history.configure(True)
    history.append("temporary")
    assert history_store.db_path().is_file()
    history.configure(False)  # turning off must leave no trace
    assert not history_store.db_path().exists()


def test_clear_wipes_disk_when_persisting():
    history.configure(True)
    history.append("a")
    history.append("b")
    history.clear()
    assert history.load_all() == []
    assert history_store.load_recent(10) == []
