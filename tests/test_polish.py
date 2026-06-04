"""Tests for transcript post-processing — slumbr/polish.py.

Covers the user-visible cleanup contract: capitalization + terminal period,
trailing-hallucination stripping (and the guard that spares a genuine short
"thank you"), whole-word case-insensitive replacements, and the runaway-
repetition collapse.
"""

from slumbr.polish import polish

# ----- regression: a user word-replacement value must be applied LITERALLY.
# A correction containing a backslash/group-ref used to crash re.sub (e.g. \1 =
# "invalid group reference", trailing "\" = "bad escape") or silently corrupt
# output (\n -> newline). It runs on every transcript, so a crash = lost paste.


def test_replacement_with_regex_backref_is_literal():
    assert polish("say foo now", replacements={"foo": r"\1"}) == r"Say \1 now."


def test_replacement_with_trailing_backslash_does_not_crash():
    out = polish("say foo now", replacements={"foo": "bar\\"})
    assert isinstance(out, str) and "bar\\" in out


def test_replacement_with_windows_path_not_corrupted():
    assert polish("open foo", replacements={"foo": r"C:\new\tmp"}) == r"Open C:\new\tmp."


def test_capitalizes_and_adds_terminal_period():
    assert polish("hello world") == "Hello world."


def test_keeps_an_existing_terminator():
    assert polish("is this on?") == "Is this on?"


def test_empty_in_empty_out():
    assert polish("") == ""
    assert polish("   ") == ""


def test_strips_trailing_filler_when_real_content_precedes():
    assert polish("this is a real sentence thank you") == "This is a real sentence."
    assert polish("okay so here is the plan thanks for watching") == ("Okay so here is the plan.")


def test_keeps_a_genuinely_short_thank_you():
    # The trailer IS the message (too few words precede it) — leave it alone.
    assert polish("thank you") == "Thank you."


def test_strip_filler_can_be_disabled():
    out = polish("this is a real sentence thank you", strip_filler=False)
    assert "thank you" in out.lower()


def test_word_replacements_whole_word_case_insensitive():
    assert polish("i love keybinde", replacements={"keybinde": "keybinds"}) == ("I love keybinds.")
    # whole-word only — a substring inside a longer word is untouched
    out = polish("keybindes are great", replacements={"keybinde": "keybinds"})
    assert "keybindes" in out.lower()


def test_collapses_runaway_repetition():
    looped = " ".join(["hello there friend"] * 8)
    out = polish(looped)
    assert out.lower().count("hello there friend") == 1


def test_spares_natural_short_repetition():
    # "no no no" is emphasis, not a runaway loop — must survive.
    assert polish("no no no that is wrong").lower().startswith("no no no")


# ----- boundary regressions for the repetition-collapse thresholds. These pin
# the tuned constants (_REPEAT_MIN_REPS_WORD=6, _REPEAT_MIN_REPS_PHRASE=4,
# _REPEAT_MIN_WORDS=8) so a future "cleanup" can't silently start eating natural
# speech or stop catching real ASR loops.


def test_single_word_loop_collapses_at_six_reps():
    # 6 consecutive identical words IS a runaway loop -> collapse to one.
    out = polish(" ".join(["you"] * 6) + " did the thing").lower()
    assert out == "you did the thing."


def test_single_word_repeated_five_times_is_spared():
    # 5 reps stays under the single-word threshold -> left as emphasis.
    out = polish("you you you you you really mean it here ok").lower()
    assert out.count("you") == 5


def test_phrase_loop_collapses_at_four_reps():
    out = polish(" ".join(["stop it now"] * 4)).lower()
    assert out == "stop it now."


def test_phrase_repeated_three_times_is_spared():
    out = polish(" ".join(["stop it now"] * 3)).lower()
    assert out.count("stop it now") == 3


def test_text_under_min_words_is_never_collapsed():
    # Below _REPEAT_MIN_WORDS (8) the text is too short to be a loop.
    assert polish("hi hi hi hi hi hi").lower() == "hi hi hi hi hi hi."
