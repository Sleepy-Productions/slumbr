"""Configurable tap-to-toggle global hotkey — single key OR a combo.

Installs a low-level Windows keyboard hook (via pynput's
``win32_event_filter``). The bound combo is 1–4 VKs that must be held
together; the toggle fires once when the last key completes the combo.

Suppression is *selective*, which is the whole reason this is subtle:

- A **single-key** bind (the classic Caps Lock default) is fully
  swallowed — tapping it never reaches the OS, so Caps Lock state never
  flips, F-keys don't fire their normal action, etc.
- In a **combo**, modifier keys (Ctrl / Shift / Alt / Win) are NEVER
  suppressed — they pass straight through so Ctrl+C and friends keep
  working everywhere. Only the non-modifier *trigger* key is consumed,
  and only on the press that actually completes the combo. Pressing that
  same trigger on its own (modifiers not held) types normally.

The filter runs inside the Windows hook thread, which has a strict
timeout — keep the callback trivial (it's just ``bridge.toggle.emit``).
The matching itself is a pure state machine (``_process``) so it can be
unit-tested without the OS hook.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from pynput import keyboard

from .keymap import MODIFIER_VKS, normalize_modifier

log = logging.getLogger(__name__)

# Windows hook messages. Hard-coded so we don't depend on win32con.
_WM_KEYDOWN = 0x0100
_WM_KEYUP = 0x0101
_WM_SYSKEYDOWN = 0x0104
_WM_SYSKEYUP = 0x0105

VK_CAPITAL = 0x14  # Caps Lock — the default hotkey


class Hotkey:
    """Suppress + fire ``on_press`` when the bound combo is held.

    Pass a list of 1–4 VKs. A single-element list is the classic
    single-key tap-to-toggle. Hold-down does not auto-repeat — the toggle
    fires once per completion and re-arms only after the combo is released.
    """

    def __init__(self, vks: list[int], on_press: Callable[[], None]) -> None:
        self._on_press = on_press
        self._listener: keyboard.Listener | None = None
        self.set_vks(vks)

    def set_vks(self, vks: list[int]) -> None:
        """Rebind. Safe to call while the hook is running."""
        norm = [normalize_modifier(v) for v in vks if v]
        self._combo: set[int] = set(norm) or {VK_CAPITAL}
        self._held: set[int] = set()
        self._suppressed: set[int] = set()  # keys whose keydown we swallowed
        self._fired = False

    # Back-compat shim for any caller still passing a single VK.
    def set_vk(self, vk: int) -> None:
        self.set_vks([vk])

    def _process(self, vk: int, is_down: bool) -> tuple[bool, bool]:
        """Pure combo state machine. ``vk`` must already be normalized.

        Returns ``(fire_toggle, suppress_event)``. No I/O — unit-testable.
        """
        if vk not in self._combo:
            return (False, False)

        fire = False
        suppress = False
        if is_down:
            others = self._combo - {vk}
            others_held = others <= self._held
            self._held.add(vk)
            completed = self._combo <= self._held
            if completed and others_held:
                # This press just completed the combo.
                if not self._fired:
                    self._fired = True
                    fire = True
                # Never swallow a modifier (would break Ctrl+C globally).
                # Swallow the trigger so the bound key doesn't also act.
                if vk not in MODIFIER_VKS:
                    suppress = True
                    self._suppressed.add(vk)
            # A non-completing press of a combo key passes through, so the
            # trigger letter still types normally when the modifiers aren't held.
        else:  # key up
            self._held.discard(vk)
            if vk in self._suppressed:
                suppress = True  # balance the swallowed keydown
                self._suppressed.discard(vk)
            if not (self._combo <= self._held):
                self._fired = False  # re-arm once the combo breaks
        return (fire, suppress)

    def _win32_filter(self, msg: int, data) -> bool:  # data is KBDLLHOOKSTRUCT
        is_down = msg in (_WM_KEYDOWN, _WM_SYSKEYDOWN)
        is_up = msg in (_WM_KEYUP, _WM_SYSKEYUP)
        if not (is_down or is_up):
            return True
        vk = normalize_modifier(data.vkCode)
        fire, suppress = self._process(vk, is_down)
        if fire:
            try:
                self._on_press()
            except Exception as e:  # noqa: BLE001
                log.error("callback raised: %r", e)
        if suppress and self._listener is not None:
            self._listener.suppress_event()  # raises SuppressException internally
            return False
        return True

    def start(self) -> None:
        if self._listener is not None:
            return
        self._listener = keyboard.Listener(win32_event_filter=self._win32_filter)
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
