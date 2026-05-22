"""Tests for `_LocalAgreement2` in `slumbr.stt.streaming_engine`.

These tests do NOT require the Moonshine/VAD/punct models — they
exercise the pure-Python LA-2 helper directly. Safe to run in CI.
"""

from __future__ import annotations

import time

from slumbr.stt.streaming_engine import _LocalAgreement2


def test_identical_passes_commit_everything() -> None:
    la2 = _LocalAgreement2()
    # First feed has no prior pass to agree with — nothing commits.
    committed, tentative = la2.feed("hello world")
    assert committed == ""
    assert tentative == "hello world"
    # Second identical feed agrees with the first — everything commits.
    committed, tentative = la2.feed("hello world")
    assert committed == "hello world"
    assert tentative == ""


def test_tail_disagrees_prefix_commits() -> None:
    la2 = _LocalAgreement2()
    la2.feed("hello world foo")
    committed, tentative = la2.feed("hello world bar")
    # "hello world" is the longest common prefix between the two passes.
    assert committed == "hello world"
    assert tentative == "bar"


def test_tail_grows_then_settles() -> None:
    la2 = _LocalAgreement2()
    la2.feed("hello")
    la2.feed("hello world")
    # Two passes both start with "hello" — that commits. "world" appears
    # only in the second so it's still tentative.
    committed, tentative = la2.feed("hello world")
    # Now the second and third agree on "hello world" entirely.
    assert committed == "hello world"
    assert tentative == ""


def test_committed_never_shrinks() -> None:
    la2 = _LocalAgreement2()
    la2.feed("hello world this is a test")
    la2.feed("hello world this is a test")
    # All committed.
    committed, _ = la2.feed("hello world this is a test")
    assert committed == "hello world this is a test"
    # Now the model walks back and produces a shorter pass. Previously
    # committed text must remain committed.
    committed, tentative = la2.feed("hello world")
    assert committed == "hello world this is a test"
    # Nothing new to render as tentative because the new pass is fully
    # absorbed by the committed prefix... actually `words` is shorter
    # than committed, so tentative is empty.
    assert tentative == ""


def test_force_commit_after_timeout() -> None:
    la2 = _LocalAgreement2(timeout_s=0.05)
    la2.feed("hello foo")
    committed, tentative = la2.feed("hello bar")
    assert committed == "hello"
    assert tentative == "bar"
    # The tentative tail is now stuck — repeated passes keep disagreeing.
    time.sleep(0.08)
    committed, tentative = la2.feed("hello baz")
    # Watchdog should force-commit the current tentative tail.
    assert "baz" in committed
    assert tentative == ""


def test_reset_clears_state() -> None:
    la2 = _LocalAgreement2()
    la2.feed("hello world")
    la2.feed("hello world")
    la2.reset()
    # After reset, feed must behave as a brand-new instance.
    committed, tentative = la2.feed("something else entirely")
    assert committed == ""
    assert tentative == "something else entirely"
