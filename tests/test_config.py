"""Tests for SlumbrConfig defaults + tolerant (de)serialization — slumbr/config.py."""

from slumbr.config import SlumbrConfig


def test_sane_defaults():
    c = SlumbrConfig()
    assert c.paste_method == "ctrl_v"
    assert c.auto_send is False
    assert c.keep_transcript_on_clipboard is False
    assert c.strip_trailing_filler is True
    # popup conveniences default ON for a friendly first run
    assert c.compact_popup is True
    assert c.popup_follow_cursor is True
    assert c.hotkey_vks == [0x14]
    assert c.hotkey_vk == 0x14


def test_from_dict_empty_gives_defaults():
    c = SlumbrConfig.from_dict({})
    assert c.hotkey_vks == [0x14]
    assert c.paste_method == "ctrl_v"


def test_from_dict_ignores_unknown_keys():
    c = SlumbrConfig.from_dict({"totally_made_up": 123, "paste_method": "type"})
    assert c.paste_method == "type"


def test_legacy_single_hotkey_migrates_to_combo():
    c = SlumbrConfig.from_dict({"hotkey_vk": 65})
    assert c.hotkey_vks == [65]
    assert c.hotkey_vk == 65


def test_hotkey_combo_trigger_is_last_element():
    c = SlumbrConfig.from_dict({"hotkey_vks": [17, 16, 74]})
    assert c.hotkey_vks == [17, 16, 74]
    assert c.hotkey_vk == 74


def test_word_replacements_coerced_and_cleaned():
    c = SlumbrConfig.from_dict({"word_replacements": {"a": "b", "": "x", "c": 5}})
    assert c.word_replacements["a"] == "b"
    assert "" not in c.word_replacements      # blank key dropped
    assert c.word_replacements["c"] == "5"    # non-str value coerced


def test_corrupt_word_replacements_falls_back_to_empty():
    c = SlumbrConfig.from_dict({"word_replacements": "not-a-dict"})
    assert c.word_replacements == {}


def test_round_trip_preserves_scalar_fields():
    c = SlumbrConfig(
        paste_method="type", auto_send=True, keep_transcript_on_clipboard=True
    )
    c2 = SlumbrConfig.from_dict(c.to_dict())
    assert c2.paste_method == "type"
    assert c2.auto_send is True
    assert c2.keep_transcript_on_clipboard is True
