"""Mic picker selection: keep best host-API instance per name + drop MME
truncation duplicates. Pure-function tests on _select_mic_devices."""

from __future__ import annotations

from slumbr.ui.tabs.voice import _select_mic_devices

# candidate tuples are (index, name, host_prio); lower host_prio = preferred


def test_collapses_mme_truncation_duplicate():
    # MME truncates at 31 chars -> "…2 S" (no ")"); DirectSound has the full name.
    raw = [
        (2, "Microphone (HyperX QuadCast 2 S)", 0),  # DirectSound, full
        (7, "Microphone (HyperX QuadCast 2 S", 1),  # MME, truncated prefix
    ]
    assert _select_mic_devices(raw) == [(2, "Microphone (HyperX QuadCast 2 S)")]


def test_keeps_best_host_instance_per_name():
    raw = [
        (9, "Microphone (Realtek HD Audio Mic input)", 3),  # WASAPI
        (4, "Microphone (Realtek HD Audio Mic input)", 0),  # DirectSound (better)
    ]
    assert _select_mic_devices(raw) == [(4, "Microphone (Realtek HD Audio Mic input)")]


def test_keeps_distinct_mics_sorted():
    raw = [
        (4, "Microphone (Realtek HD Audio Mic input)", 0),
        (2, "Microphone (HyperX QuadCast 2 S)", 0),
    ]
    assert _select_mic_devices(raw) == [
        (2, "Microphone (HyperX QuadCast 2 S)"),
        (4, "Microphone (Realtek HD Audio Mic input)"),
    ]


def test_empty():
    assert _select_mic_devices([]) == []
