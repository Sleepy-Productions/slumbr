"""Reverse-PTT keybind sender.

When the user configures a "push-to-mute" keybind in another app
(Discord's Push-To-Mute, OBS hotkey, etc.), Slumbr can press that
keybind for the duration of a dictation session so the other app
silences the mic — while Slumbr's own capture stream continues
untouched.

This is a deliberate workaround, not a real solution: Windows has no
API to mute a mic for specific applications. The proper fix is virtual
audio routing via VB-Cable (Phase 3 backlog item). This file gets us
Discord-grade reverse-PTT for the prototype period.

Contract:
  - ``arm(vk)`` records the VK that should be pressed when dictation
    starts. ``disarm()`` clears it. Setting ``vk`` to 0 / None disarms.
  - ``press()`` and ``release()`` are idempotent — calling press twice
    in a row sends one key event, calling release with nothing held
    is a no-op. This is load-bearing for the state machine: failed
    transcripts call ``_reset_to_idle`` and we want a stuck Ctrl-Shift-M
    key never to be possible.
  - ``release()`` is also called in app shutdown so a crash mid-dictation
    doesn't leave the mute key held forever.
"""

from __future__ import annotations

import logging

from pynput.keyboard import Controller, KeyCode

log = logging.getLogger(__name__)


class MuteKeyController:
    def __init__(self) -> None:
        self._controller = Controller()
        self._armed_vk: int | None = None
        self._held = False

    def arm(self, vk: int | None) -> None:
        """Set or clear the VK to press during dictation."""
        # Release any currently-held key before re-arming so we never
        # leave a key stuck when the user changes their config mid-dict.
        if self._held:
            self.release()
        self._armed_vk = vk if vk else None
        log.debug("mute-key armed=%s", self._armed_vk)

    def disarm(self) -> None:
        self.arm(None)

    @property
    def armed(self) -> bool:
        return self._armed_vk is not None

    def press(self) -> None:
        """Press the armed key. No-op if disarmed or already held."""
        if self._armed_vk is None or self._held:
            return
        try:
            self._controller.press(KeyCode.from_vk(self._armed_vk))
            self._held = True
            log.debug("mute-key press vk=%#x", self._armed_vk)
        except Exception as e:  # noqa: BLE001
            log.warning("mute-key press failed: %s", e)

    def release(self) -> None:
        """Release the armed key. No-op if nothing held."""
        if self._armed_vk is None or not self._held:
            self._held = False
            return
        try:
            self._controller.release(KeyCode.from_vk(self._armed_vk))
            log.debug("mute-key release vk=%#x", self._armed_vk)
        except Exception as e:  # noqa: BLE001
            log.warning("mute-key release failed: %s", e)
        finally:
            self._held = False
