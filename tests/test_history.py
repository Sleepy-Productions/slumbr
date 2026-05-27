"""In-memory, ephemeral transcript history (slumbr/history.py).

History holds at most MAX_ENTRIES; reaching the cap clears the list (the new
entry starts fresh). Nothing is written to disk — it's gone when the process
ends.
"""

from __future__ import annotations

import pytest

from slumbr import history


@pytest.fixture(autouse=True)
def _clean_history():
    history.clear()
    yield
    history.clear()


def test_cap_is_fifty():
    assert history.MAX_ENTRIES == 50


def test_appends_oldest_first_and_latest():
    history.append("a")
    history.append("b")
    assert [e.text for e in history.load_all()] == ["a", "b"]
    assert history.latest() == "b"


def test_fills_to_cap():
    for i in range(history.MAX_ENTRIES):
        history.append(f"t{i}")
    assert len(history.load_all()) == history.MAX_ENTRIES


def test_clears_and_starts_fresh_on_overflow():
    for i in range(history.MAX_ENTRIES):
        history.append(f"t{i}")
    history.append("overflow")  # the cap+1 th — wipes, new entry is #1
    live = history.load_all()
    assert len(live) == 1
    assert live[0].text == "overflow"
    assert history.latest() == "overflow"


def test_clear_empties():
    history.append("x")
    history.clear()
    assert history.load_all() == []
    assert history.latest() == ""


def test_whitespace_or_empty_ignored():
    history.append("   ")
    history.append("")
    assert history.load_all() == []


def test_nothing_written_to_disk(tmp_path, monkeypatch):
    # In-memory by design: appending must not create ANY file under APPDATA.
    monkeypatch.setenv("APPDATA", str(tmp_path))
    history.append("a private thought")
    assert list(tmp_path.rglob("*")) == []  # nothing persisted anywhere
