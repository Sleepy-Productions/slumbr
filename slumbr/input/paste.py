from __future__ import annotations

import time

import pyperclip
from pynput.keyboard import Controller, Key

_kbd = Controller()

# Delay between SetClipboard and Ctrl+V. Without this, some apps (Electron-based
# chat clients, certain browsers) miss the paste because the clipboard handle is
# still in flight.
_PRE_PASTE_DELAY_S = 0.03
# Delay between Ctrl+V and clipboard restore. Without it, restore wins the race
# and the user sees the previous clipboard content pasted.
_POST_PASTE_DELAY_S = 0.08


def paste_text(
    text: str,
    *,
    auto_send: bool = False,
    preserve_clipboard: bool = False,
) -> None:
    if not text:
        return

    snapshot: str | None = None
    if preserve_clipboard:
        try:
            snapshot = pyperclip.paste()
        except Exception as e:  # noqa: BLE001
            print(f"[paste] could not snapshot clipboard: {e}")
            snapshot = None

    pyperclip.copy(text)
    time.sleep(_PRE_PASTE_DELAY_S)

    with _kbd.pressed(Key.ctrl):
        _kbd.press("v")
        _kbd.release("v")

    if auto_send:
        time.sleep(0.05)
        _kbd.press(Key.enter)
        _kbd.release(Key.enter)

    if preserve_clipboard and snapshot is not None:
        time.sleep(_POST_PASTE_DELAY_S)
        try:
            pyperclip.copy(snapshot)
        except Exception as e:  # noqa: BLE001
            print(f"[paste] could not restore clipboard: {e}")
