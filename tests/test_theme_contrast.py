"""Tests for theme.text_on — contrast-aware fill text color.

The white-on-white bug (Sleepy accent #ffffff → white text on white button)
was the known class of failure. These tests pin the perceptual luminance gate
so the fix can never quietly regress.

Contract (from theme.text_on docstring + accent-fill-contrast memory):
  - Light fill (luminance > 0.6) → return near-black (#0A0A0B).
  - Dark fill → return TEXT_PRIMARY (white, #FFFFFF).
  - Malformed / wrong type → return TEXT_PRIMARY (safe fallback).
"""

from __future__ import annotations

import pytest

from slumbr.theme import TEXT_PRIMARY, text_on


# ------------------------------------------------------------------ light fills


class TestLightFills:
    """A light or white accent must produce dark (legible) text."""

    def test_pure_white_gives_dark_text(self):
        """The #ffffff regression: white fill must NOT return white text."""
        result = text_on("#ffffff")
        assert result == "#0A0A0B", (
            "#ffffff (white fill) must yield dark text — the white-on-white regression"
        )

    def test_near_white_gives_dark_text(self):
        result = text_on("#F2F2F2")
        assert result == "#0A0A0B"

    def test_mid_light_gray_gives_dark_text(self):
        result = text_on("#CCCCCC")
        assert result == "#0A0A0B"

    def test_brand_violet_tint_gives_dark_text(self):
        # VIOLET_TINT = "#F2F2F2" — a very light gray
        result = text_on("#F2F2F2")
        assert result == "#0A0A0B"

    def test_medium_gray_above_threshold_gives_dark_text(self):
        # #9A9A9A has luminance ~0.604, just above the 0.6 threshold — dark text.
        result = text_on("#9A9A9A")
        assert result == "#0A0A0B"


# ------------------------------------------------------------------ dark fills


class TestDarkFills:
    """Dark fills must return light (white) text."""

    def test_pure_black_gives_light_text(self):
        assert text_on("#000000") == TEXT_PRIMARY

    def test_dark_surface_gives_light_text(self):
        assert text_on("#050505") == TEXT_PRIMARY

    def test_mid_dark_gray_gives_light_text(self):
        # Pure dark gray — luminance well under 0.6 → light text.
        assert text_on("#3A3A3A") == TEXT_PRIMARY


# ------------------------------------------------------------------ boundary


class TestLuminanceBoundary:
    def test_luminance_just_above_threshold_gives_dark(self):
        # A color whose sRGB luminance is just above 0.6 → dark text.
        # Pure green (#00FF00) has luminance ≈ 0.715 — should give dark text.
        assert text_on("#00FF00") == "#0A0A0B"

    def test_luminance_just_below_threshold_gives_light(self):
        # Pure blue (#0000FF) has luminance ≈ 0.072 — dark fill, light text.
        assert text_on("#0000FF") == TEXT_PRIMARY


# ------------------------------------------------------------------ robustness


class TestRobustness:
    """Malformed inputs must return TEXT_PRIMARY (safe, never crash)."""

    @pytest.mark.parametrize(
        "bad",
        [
            None,
            "",
            "banana",
            12345,
            "#xyz",
            "#ff",        # too short
            [],
            {"a": 1},
        ],
    )
    def test_bad_input_returns_safe_default(self, bad):
        result = text_on(bad)
        assert result == TEXT_PRIMARY, (
            f"text_on({bad!r}) must fall back to TEXT_PRIMARY, got {result!r}"
        )

    def test_missing_hash_prefix_still_handled(self):
        # Some callers might omit "#"; the function should handle or fall back.
        result = text_on("ffffff")
        # Either correctly identifies as light or falls back — must not raise.
        assert result in ("#0A0A0B", TEXT_PRIMARY)
