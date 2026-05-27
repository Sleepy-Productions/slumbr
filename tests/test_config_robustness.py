"""Config + accent robustness against corrupt / hand-edited config.json.

Regression for the crash where a non-string ``accent_color`` (a number or null
in config.json) slipped through load() and later crashed derive_accent() on
``.lstrip()`` when the UI built.
"""

from __future__ import annotations

import pytest

from slumbr import config as cfgmod
from slumbr.config import SlumbrConfig
from slumbr.theme import derive_accent, text_on


@pytest.fixture
def tmpcfg(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", tmp_path / "config.json")
    return tmp_path


def _write(tmpcfg, content: str) -> None:
    (tmpcfg / "config.json").write_text(content, encoding="utf-8")


@pytest.mark.parametrize("content", [
    "{ not json",
    "",
    "   ",
    "[1,2,3]",
    "42",
    "null",
    '{"accent_color": 12345}',
    '{"accent_color": null}',
    '{"hotkey_vks": "nope"}',
    '{"word_replacements": "x"}',
    '{"accent_color": "#abcdef", "bogus_future_key": 1}',
    '{"language": "es"}',
])
def test_load_never_crashes_and_yields_usable_accent(tmpcfg, content):
    _write(tmpcfg, content)
    cfg = SlumbrConfig.load()
    assert isinstance(cfg, SlumbrConfig)
    # accent must always end up a non-empty string...
    assert isinstance(cfg.accent_color, str) and cfg.accent_color.strip()
    # ...and must not blow up the theme pipeline.
    assert derive_accent(cfg.accent_color)[0].startswith("#")


def test_derive_accent_and_text_on_survive_bad_types():
    for bad in (12345, None, "banana", "", [1, 2, 3], {"a": 1}):
        assert derive_accent(bad)[0].startswith("#")
        assert text_on(bad).startswith("#")


def test_unknown_key_preserves_valid_accent(tmpcfg):
    _write(tmpcfg, '{"accent_color": "#abcdef", "bogus_future_key": 1}')
    assert SlumbrConfig.load().accent_color == "#abcdef"


def test_partial_config_keeps_set_field(tmpcfg):
    _write(tmpcfg, '{"language": "es"}')
    assert SlumbrConfig.load().language == "es"
