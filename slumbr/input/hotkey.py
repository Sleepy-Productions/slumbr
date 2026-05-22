"""Configurable tap-to-toggle global hotkey with full OS suppression.

Installs a low-level Windows keyboard hook (via pynput's
`win32_event_filter`) and *swallows* every press of the chosen key
before the OS sees it. So while Slumbr is running, tapping the bound
key fires our toggle callback and does NOT pass through (Caps Lock
state is never flipped, F-keys don't trigger their normal action, etc.).
When Slumbr exits, the hook is uninstalled and the key behaves normally
again.

This was chosen over pynput's `GlobalHotKeys` because (a) `GlobalHotKeys`
combo-matching has been flaky for our combos on Win11, and (b) for a
single-key toggle we want to consume the key, not let it through.

The filter runs inside the Windows hook thread, which has a strict
timeout — Windows silently unhooks slow filters. Keep the callback
trivial (e.g. `bridge.toggle.emit`, which is just a Qt signal post).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from pynput import keyboard

log = logging.getLogger(__name__)

# Windows hook messages. Hard-coded so we don't depend on win32con (which
# would pull in pywin32). Values are stable Windows ABI.
_WM_KEYDOWN = 0x0100
_WM_KEYUP = 0x0101
_WM_SYSKEYDOWN = 0x0104
_WM_SYSKEYUP = 0x0105

VK_CAPITAL = 0x14  # Caps Lock — the default hotkey


class Hotkey:
    """Suppress a single VK and fire `on_press` on each tap.

    Hold-down does not auto-repeat — only the initial press of each
    physical tap fires.
    """

    def __init__(self, vk: int, on_press: Callable[[], None]) -> None:
        self._vk = vk
        self._on_press = on_press
        self._listener: keyboard.Listener | None = None
        self._held = False

    def set_vk(self, vk: int) -> None:
        """Rebind to a different VK. Safe to call while the hook is running."""
        self._vk = vk
        self._held = False

    def _win32_filter(self, msg: int, data) -> bool:  # data is KBDLLHOOKSTRUCT
        if data.vkCode != self._vk:
            return True

        # Run our toggle callback FIRST, then ask pynput to suppress. We do
        # the suppression last because pynput's `suppress_event()` works by
        # raising an internal `SuppressException` that propagates up through
        # this filter and is caught by pynput's hook loop. If we called
        # `suppress_event()` first, that exception would skip the toggle.
        if msg in (_WM_KEYDOWN, _WM_SYSKEYDOWN):
            if not self._held:
                self._held = True
                try:
                    self._on_press()
                except Exception as e:  # noqa: BLE001
                    log.error("callback raised: %r", e)
        elif msg in (_WM_KEYUP, _WM_SYSKEYUP):
            self._held = False

        if self._listener is not None:
            self._listener.suppress_event()  # raises SuppressException internally
        return False

    def start(self) -> None:
        if self._listener is not None:
            return
        self._listener = keyboard.Listener(win32_event_filter=self._win32_filter)
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
