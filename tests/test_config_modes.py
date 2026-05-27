"""Tests for switchable dictation modes — slumbr/config.py + slumbr/formatting.py.

Locks in the migration contract (an old, pre-modes config upgrades cleanly into
the three built-ins with the user's settings folded into Notes), round-trip
stability, the active_mode clamp, and that the formatter dispatch follows the
active mode.
"""

from slumbr.config import ModeProfile, SlumbrConfig, _default_modes
from slumbr.formatting import format_transcript


def test_fresh_config_has_three_builtin_modes() -> None:
    cfg = SlumbrConfig()
    assert [m.id for m in cfg.modes] == ["notes", "llm_chat", "code"]
    assert cfg.active_mode == "notes"
    assert cfg.active_profile().formatter == "prose"


def test_builtin_personas() -> None:
    modes = {m.id: m for m in _default_modes()}
    assert modes["code"].formatter == "code"
    assert modes["llm_chat"].auto_send is True
    assert modes["notes"].auto_send is False
    assert modes["code"].strip_trailing_filler is False


def test_legacy_config_seeds_modes_and_folds_globals_into_notes() -> None:
    # A pre-modes config: top-level knobs, no "modes" key.
    legacy = {
        "language": "es",
        "initial_prompt": "kubectl, async",
        "word_replacements": {"keybinde": "keybinds"},
        "auto_send": True,
        "paste_method": "type",
    }
    cfg = SlumbrConfig.from_dict(legacy)
    assert [m.id for m in cfg.modes] == ["notes", "llm_chat", "code"]
    notes = cfg.active_profile()
    assert notes.id == "notes"
    assert notes.language == "es"
    assert notes.initial_prompt == "kubectl, async"
    assert notes.word_replacements == {"keybinde": "keybinds"}
    assert notes.auto_send is True
    assert notes.paste_method == "type"
    # The other built-ins are untouched by the legacy globals.
    assert cfg.modes[2].formatter == "code"


def test_round_trip_is_stable() -> None:
    cfg = SlumbrConfig()
    cfg.active_mode = "code"
    again = SlumbrConfig.from_dict(cfg.to_dict())
    assert [m.id for m in again.modes] == ["notes", "llm_chat", "code"]
    assert again.active_mode == "code"
    assert again.active_profile().formatter == "code"


def test_unknown_keys_ignored() -> None:
    cfg = SlumbrConfig.from_dict({"totally_made_up": 123, "active_mode": "code"})
    assert cfg.active_mode == "code"
    assert not hasattr(cfg, "totally_made_up")


def test_active_mode_clamped_to_existing_id() -> None:
    cfg = SlumbrConfig.from_dict({"active_mode": "bogus"})
    assert cfg.active_mode == "notes"


def test_missing_builtin_is_restored() -> None:
    # A saved config that somehow only has one mode → built-ins re-added.
    cfg = SlumbrConfig.from_dict(
        {"modes": [{"id": "notes", "label": "Notes", "formatter": "prose"}]}
    )
    assert {m.id for m in cfg.modes} == {"notes", "llm_chat", "code"}


def test_malformed_mode_entries_dropped() -> None:
    cfg = SlumbrConfig.from_dict({"modes": [{"label": "no id"}, "not a dict", 42]})
    # No usable entries → fall back to the full built-in set.
    assert [m.id for m in cfg.modes] == ["notes", "llm_chat", "code"]


def test_cycle_mode_vks_coerced() -> None:
    cfg = SlumbrConfig.from_dict({"cycle_mode_vks": [0x11, 0x10, "junk", 77]})
    assert cfg.cycle_mode_vks == [0x11, 0x10, 77]


def test_format_transcript_follows_active_mode() -> None:
    prose = ModeProfile(id="notes", label="Notes", formatter="prose")
    code = ModeProfile(id="code", label="Code", formatter="code")
    assert format_transcript("hello world", prose) == "Hello world."
    assert format_transcript("foo open paren bar close paren", code) == "foo(bar)"
