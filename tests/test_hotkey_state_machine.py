"""Tests for Hotkey._process — the pure combo state machine.

``_process`` is the most load-bearing pure function in Slumbr: every reverse-PTT
toggle goes through it. The method does no I/O (no OS hook, no pynput), so it
is safe to drive headlessly at any scale.

Coverage targets:
- Single-key (CapsLock default): fires once, suppresses key, re-arms on release.
- Combo (modifier + trigger): fires once, suppresses only the trigger key,
  re-arms once the combo breaks.
- Hold-down auto-repeat guard: a second down while held must NOT fire again.
- Modifier key in combo: the modifier press itself must NEVER be suppressed.
- Unrelated keys: no fire, no suppress.
- Re-arm semantics: after the combo releases, the next press fires again.
- Left/right modifier variants are normalized (0xA2 == Ctrl is in a Ctrl combo).
"""

from __future__ import annotations

import pytest

from slumbr.input.hotkey import Hotkey

# Reusable no-op callback — _process doesn't call it (it's returned as a flag),
# but Hotkey.__init__ requires one.
_NOOP = lambda: None  # noqa: E731

VK_CAPS = 0x14
VK_CTRL = 0x11
VK_SHIFT = 0x10
VK_J = 0x4A
VK_LCTRL = 0xA2  # left ctrl — should normalize to 0x11


# ------------------------------------------------------------------ helpers


def _hk(*vks: int) -> Hotkey:
    """Build a Hotkey bound to ``vks`` with a no-op callback."""
    return Hotkey(list(vks), _NOOP)


# ================================================================== single key


class TestSingleKey:
    """CapsLock default — the universal path every user hits."""

    def test_fires_on_first_down(self):
        hk = _hk(VK_CAPS)
        fire, suppress = hk._process(VK_CAPS, True)
        assert fire is True

    def test_suppresses_on_first_down(self):
        hk = _hk(VK_CAPS)
        _, suppress = hk._process(VK_CAPS, True)
        assert suppress is True

    def test_no_fire_on_hold_repeat(self):
        """Second key-down while still held must not fire again (no auto-repeat)."""
        hk = _hk(VK_CAPS)
        hk._process(VK_CAPS, True)  # first press — fires
        fire, _ = hk._process(VK_CAPS, True)  # held — must NOT fire
        assert fire is False

    def test_suppresses_matching_key_up(self):
        """The key-up must also be suppressed to balance the swallowed key-down."""
        hk = _hk(VK_CAPS)
        hk._process(VK_CAPS, True)
        _, suppress = hk._process(VK_CAPS, False)
        assert suppress is True

    def test_rearms_after_release(self):
        """After the key is released, the next press fires again."""
        hk = _hk(VK_CAPS)
        hk._process(VK_CAPS, True)
        hk._process(VK_CAPS, False)  # release — re-arms
        fire, _ = hk._process(VK_CAPS, True)
        assert fire is True

    def test_unrelated_key_no_effect(self):
        hk = _hk(VK_CAPS)
        fire, suppress = hk._process(VK_J, True)
        assert fire is False
        assert suppress is False


# ================================================================== combo key


class TestCombo:
    """Ctrl + J — modifier + trigger combo."""

    def _ctrl_j(self) -> Hotkey:
        return _hk(VK_CTRL, VK_J)

    def test_trigger_alone_does_not_fire(self):
        hk = self._ctrl_j()
        fire, _ = hk._process(VK_J, True)
        assert fire is False

    def test_modifier_alone_does_not_fire(self):
        hk = self._ctrl_j()
        fire, _ = hk._process(VK_CTRL, True)
        assert fire is False

    def test_modifier_then_trigger_fires(self):
        hk = self._ctrl_j()
        hk._process(VK_CTRL, True)
        fire, _ = hk._process(VK_J, True)
        assert fire is True

    def test_trigger_suppressed_modifier_not_suppressed(self):
        """The trigger (J) must be swallowed; the modifier (Ctrl) must pass through."""
        hk = self._ctrl_j()
        _, ctrl_suppress = hk._process(VK_CTRL, True)
        _, j_suppress = hk._process(VK_J, True)
        assert ctrl_suppress is False, "modifier must NEVER be suppressed"
        assert j_suppress is True, "trigger key must be suppressed"

    def test_no_fire_on_hold_repeat_combo(self):
        hk = self._ctrl_j()
        hk._process(VK_CTRL, True)
        hk._process(VK_J, True)  # fires
        fire, _ = hk._process(VK_J, True)  # held — must NOT fire again
        assert fire is False

    def test_rearms_when_combo_breaks(self):
        """Releasing either key breaks the combo and re-arms for the next press."""
        hk = self._ctrl_j()
        hk._process(VK_CTRL, True)
        hk._process(VK_J, True)  # fires
        hk._process(VK_J, False)  # release trigger — combo breaks, re-arms
        hk._process(VK_CTRL, True)  # re-hold modifier
        fire, _ = hk._process(VK_J, True)  # next completion must fire again
        assert fire is True

    def test_trigger_release_suppressed_to_balance_keydown(self):
        hk = self._ctrl_j()
        hk._process(VK_CTRL, True)
        hk._process(VK_J, True)
        _, suppress = hk._process(VK_J, False)
        assert suppress is True


# ================================================================== modifier normalization


class TestModifierNormalization:
    """normalize_modifier() is applied by _win32_filter before _process is called.

    _process itself receives already-normalized VKs (documented: "vk must already
    be normalized"). The normalization contract is tested here at the
    normalize_modifier level + via set_vks (which also normalizes on bind).
    """

    def test_normalize_modifier_folds_lctrl_to_ctrl(self):
        from slumbr.input.keymap import normalize_modifier
        assert normalize_modifier(VK_LCTRL) == VK_CTRL

    def test_normalize_modifier_passes_through_non_modifier(self):
        from slumbr.input.keymap import normalize_modifier
        assert normalize_modifier(VK_J) == VK_J

    def test_set_vks_normalizes_lctrl_at_bind_time(self):
        """set_vks normalizes VKs so a combo bound with 0xA2 is stored as 0x11."""
        hk = Hotkey([VK_LCTRL, VK_J], _NOOP)
        # The combo set must contain the normalized Ctrl (0x11), not raw LCtrl.
        assert VK_CTRL in hk._combo, "LCtrl must be normalized to Ctrl at bind time"
        assert VK_LCTRL not in hk._combo

    def test_combo_bound_with_lctrl_fires_on_normalized_ctrl(self):
        """When bound with 0xA2, _process receives the normalized 0x11 (from
        _win32_filter) and the combo fires correctly."""
        hk = Hotkey([VK_LCTRL, VK_J], _NOOP)
        # _process receives already-normalized VKs.
        hk._process(VK_CTRL, True)   # normalized 0x11
        fire, _ = hk._process(VK_J, True)
        assert fire is True


# ================================================================== three-key combo


class TestThreeKeyCombo:
    """Ctrl + Shift + J — ensure partial holds don't fire early."""

    def _ctrl_shift_j(self) -> Hotkey:
        return _hk(VK_CTRL, VK_SHIFT, VK_J)

    def test_partial_hold_does_not_fire(self):
        hk = self._ctrl_shift_j()
        hk._process(VK_CTRL, True)
        fire, _ = hk._process(VK_J, True)  # Shift not held — must not fire
        assert fire is False

    def test_full_combo_fires(self):
        hk = self._ctrl_shift_j()
        hk._process(VK_CTRL, True)
        hk._process(VK_SHIFT, True)
        fire, _ = hk._process(VK_J, True)
        assert fire is True

    def test_neither_modifier_suppressed(self):
        hk = self._ctrl_shift_j()
        _, s1 = hk._process(VK_CTRL, True)
        _, s2 = hk._process(VK_SHIFT, True)
        assert s1 is False
        assert s2 is False

    def test_only_trigger_suppressed(self):
        hk = self._ctrl_shift_j()
        hk._process(VK_CTRL, True)
        hk._process(VK_SHIFT, True)
        _, suppress = hk._process(VK_J, True)
        assert suppress is True


# ================================================================== rebind


class TestRebind:
    """set_vks() changes the active combo without restarting the listener."""

    def test_old_binding_no_longer_fires_after_rebind(self):
        hk = _hk(VK_CAPS)
        hk.set_vks([VK_J])
        fire, _ = hk._process(VK_CAPS, True)
        assert fire is False

    def test_new_binding_fires_after_rebind(self):
        hk = _hk(VK_CAPS)
        hk.set_vks([VK_J])
        fire, _ = hk._process(VK_J, True)
        assert fire is True
