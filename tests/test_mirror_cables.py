"""Cable selection: prefer the plain stereo endpoint, hide the multichannel
"<N>ch" sibling. Pure-function tests on _select_cables (no audio devices)."""

from __future__ import annotations

from slumbr.audio.mirror import _select_cables

# candidate tuples are (index, name, kw_prio, host_prio) — already priority-sorted


def test_hides_16ch_sibling_when_plain_exists():
    # Real-world names: the 16ch endpoint truncates "Input" -> "In".
    cands = [
        (27, "CABLE Input (VB-Audio Virtual Cable)", 0, 0),
        (24, "CABLE In 16ch (VB-Audio Virtual Cable)", 0, 0),
    ]
    assert _select_cables(cands) == [(27, "CABLE Input (VB-Audio Virtual Cable)")]


def test_keeps_16ch_when_it_is_the_only_cable():
    cands = [(24, "CABLE In 16ch (VB-Audio Virtual Cable)", 0, 0)]
    assert _select_cables(cands) == [(24, "CABLE In 16ch (VB-Audio Virtual Cable)")]


def test_dedupes_exact_name_across_host_apis():
    cands = [
        (5, "CABLE Input (VB-Audio Virtual Cable)", 0, 0),
        (9, "CABLE Input (VB-Audio Virtual Cable)", 0, 1),  # same name, DSound
    ]
    assert _select_cables(cands) == [(5, "CABLE Input (VB-Audio Virtual Cable)")]


def test_keeps_distinct_plain_cables():
    cands = [
        (1, "CABLE Input (VB-Audio Virtual Cable)", 0, 0),
        (2, "VoiceMeeter Input (VB-Audio VoiceMeeter VAIO)", 3, 0),
    ]
    assert _select_cables(cands) == [
        (1, "CABLE Input (VB-Audio Virtual Cable)"),
        (2, "VoiceMeeter Input (VB-Audio VoiceMeeter VAIO)"),
    ]


def test_empty():
    assert _select_cables([]) == []
