from __future__ import annotations

from collections.abc import Callable

from pynput import keyboard


class HotkeyListener:
    """Wraps `pynput.keyboard.GlobalHotKeys`.

    The callback runs on pynput's own input thread — do *not* do heavy work or
    touch UI directly from it. Put an event on a queue and return immediately.
    """

    def __init__(self, combo: str, on_press: Callable[[], None]) -> None:
        self.combo = combo
        self.on_press = on_press
        self._listener: keyboard.GlobalHotKeys | None = None

    def start(self) -> None:
        if self._listener is not None:
            return
        self._listener = keyboard.GlobalHotKeys({self.combo: self.on_press})
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
