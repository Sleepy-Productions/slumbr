from __future__ import annotations

import logging
import time

import pyperclip
from pynput.keyboard import Controller, Key

from .foreground import current_foreground, restore_foreground

log = logging.getLogger(__name__)

_kbd = Controller()

# Delay between SetClipboard and Ctrl+V. Without this, some apps (Electron-based
# chat clients, certain browsers) miss the paste because the clipboard handle is
# still in flight. VS Code's chat and integrated terminal both need this — do
# not lower it without testing those specifically.
_PRE_PASTE_DELAY_S = 0.03
# Delay between Ctrl+V and clipboard restore. Without it, restore wins the race
# and the user sees the previous clipboard content pasted.
_POST_PASTE_DELAY_S = 0.08
# After SetForegroundWindow, let Windows actually finish the focus change
# before sending keys. We can't safely trim this below 60 ms — even when
# the app has already done an early restore_foreground at stop time, the
# early call can silently fail (Windows foreground-locking rules) and
# this is the insurance path. Skimping causes "nothing pastes at all"
# into Electron targets like VS Code.
_FOCUS_RESTORE_DELAY_S = 0.06
# Delay between Ctrl+V and the auto-send Enter. 50 ms is too short for
# React-backed inputs (VS Code chat, GitHub issues, Discord) — the paste
# event is dispatched but the controlled-input state hasn't updated yet,
# so Enter fires against an empty box. 150 ms covers those reliably while
# still feeling instant.
_AUTO_SEND_DELAY_S = 0.15

# Recognized paste_method values; anything else falls back to "ctrl_v".
PASTE_METHODS = ("ctrl_v", "ctrl_shift_v", "type")


def _send_via_clipboard(text: str, *, with_shift: bool, preserve_clipboard: bool) -> None:
    """Stash text on the clipboard, fire Ctrl[+Shift]+V, optionally restore."""
    snapshot: str | None = None
    if preserve_clipboard:
        try:
            snapshot = pyperclip.paste()
        except Exception as e:  # noqa: BLE001
            log.warning("could not snapshot clipboard: %s", e)
            snapshot = None

    pyperclip.copy(text)
    try:
        readback = pyperclip.paste()
    except Exception as e:  # noqa: BLE001
        readback = f"<read failed: {e}>"
    log.debug("clipboard set (verify match=%s)", readback == text)
    time.sleep(_PRE_PASTE_DELAY_S)

    if with_shift:
        with _kbd.pressed(Key.ctrl, Key.shift):
            _kbd.press("v")
            _kbd.release("v")
        log.debug("sent Ctrl+Shift+V")
    else:
        with _kbd.pressed(Key.ctrl):
            _kbd.press("v")
            _kbd.release("v")
        log.debug("sent Ctrl+V")

    if preserve_clipboard and snapshot is not None:
        time.sleep(_POST_PASTE_DELAY_S)
        try:
            pyperclip.copy(snapshot)
        except Exception as e:  # noqa: BLE001
            log.warning("could not restore clipboard: %s", e)


def _send_via_typing(text: str) -> None:
    """Type each character via SendInput. Universal — works in terminals,
    chats, web inputs — but slower than paste and bypasses the clipboard.
    """
    log.debug("typing %d chars", len(text))
    _kbd.type(text)


def paste_text(
    text: str,
    *,
    auto_send: bool = False,
    preserve_clipboard: bool = False,
    target_hwnd: int | None = None,
    skip_focus_restore: bool = False,
    paste_method: str = "ctrl_v",
) -> None:
    if not text:
        return

    if paste_method not in PASTE_METHODS:
        log.warning("unknown paste_method %r, falling back to ctrl_v", paste_method)
        paste_method = "ctrl_v"

    fg_before = current_foreground()
    log.debug(
        "fg_before hwnd=%s class=%r title=%r", fg_before[0], fg_before[1], fg_before[2]
    )

    if target_hwnd and not skip_focus_restore:
        ok = restore_foreground(target_hwnd)
        log.debug("restore_foreground(%s) -> %s", target_hwnd, ok)
        time.sleep(_FOCUS_RESTORE_DELAY_S)
        fg_after = current_foreground()
        log.debug(
            "fg_after  hwnd=%s class=%r title=%r", fg_after[0], fg_after[1], fg_after[2]
        )
        if fg_after[0] != target_hwnd:
            log.warning(
                "foreground did not become target (%s); paste may miss", target_hwnd
            )

    log.debug("method=%s", paste_method)
    if paste_method == "type":
        _send_via_typing(text)
    elif paste_method == "ctrl_shift_v":
        _send_via_clipboard(text, with_shift=True, preserve_clipboard=preserve_clipboard)
    else:
        _send_via_clipboard(text, with_shift=False, preserve_clipboard=preserve_clipboard)

    if auto_send:
        time.sleep(_AUTO_SEND_DELAY_S)
        _kbd.press(Key.enter)
        _kbd.release(Key.enter)
