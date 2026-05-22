"""System-tray icon — Slumbr's only persistent UI chrome.

Provides:
- A violet-toned dot icon whose color reflects the current state
  (gray = idle, primary violet = recording, deep violet = transcribing/pasting).
- A right-click menu: Toggle Recording / Settings / Quit.

pystray runs its own event loop in a dedicated thread (`run_detached`).
Menu callbacks fire on the pystray thread — *do not* touch Qt widgets
from them. The app wires them through a Qt signal so they land on the
main thread.
"""

from __future__ import annotations

from collections.abc import Callable

import pystray
from PIL import Image, ImageDraw

from ..state import State
from ..theme import (
    COLOR_IDLE,
    COLOR_PASTING,
    COLOR_RECORDING,
    COLOR_TRANSCRIBING,
)

_ICON_SIZE = 64

_STATE_COLORS: dict[State, str] = {
    State.IDLE: COLOR_IDLE,
    State.RECORDING: COLOR_RECORDING,
    State.TRANSCRIBING: COLOR_TRANSCRIBING,
    State.PASTING: COLOR_PASTING,
}


def _icon_image(color: str) -> Image.Image:
    img = Image.new("RGBA", (_ICON_SIZE, _ICON_SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # Outer subtle halo at low alpha (visible against light AND dark taskbars).
    halo = tuple(int(color[i : i + 2], 16) for i in (1, 3, 5)) + (90,)
    d.ellipse((0, 0, _ICON_SIZE, _ICON_SIZE), fill=halo)
    d.ellipse((6, 6, _ICON_SIZE - 6, _ICON_SIZE - 6), fill=color)
    return img


class SlumbrTray:
    def __init__(
        self,
        on_toggle: Callable[[], None],
        on_show_window: Callable[[], None],
        on_settings: Callable[[], None],
        on_quit: Callable[[], None],
        hotkey_label: str = "Caps Lock",
    ) -> None:
        self._on_toggle = on_toggle
        self._on_show_window = on_show_window
        self._on_settings = on_settings
        self._on_quit = on_quit
        self._icon: pystray.Icon | None = None
        self._state = State.IDLE
        self._hotkey_label = hotkey_label

    def set_hotkey_label(self, label: str) -> None:
        """Update the tray tooltip when the hotkey is rebound from the hub."""
        self._hotkey_label = label
        if self._icon is not None:
            self._icon.title = self._title_for_state(self._state)

    def _title_for_state(self, state: State) -> str:
        if state is State.IDLE:
            return f"Slumbr — Idle (tap {self._hotkey_label} to dictate)"
        return f"Slumbr — {state.value.capitalize()}"

    def _build_menu(self) -> pystray.Menu:
        # Default item (bold, fires on left-click) is "Show Slumbr" — that
        # matches Windows convention where left-clicking a tray icon opens
        # the app, while right-click is the full menu including Toggle.
        return pystray.Menu(
            pystray.MenuItem(
                "Show Slumbr",
                lambda _icon, _item: self._on_show_window(),
                default=True,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Toggle Recording", lambda _icon, _item: self._on_toggle()
            ),
            pystray.MenuItem("Settings…", lambda _icon, _item: self._on_settings()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit Slumbr", lambda _icon, _item: self._on_quit()),
        )

    def start(self) -> None:
        if self._icon is not None:
            return
        self._icon = pystray.Icon(
            "slumbr",
            icon=_icon_image(_STATE_COLORS[State.IDLE]),
            title=self._title_for_state(State.IDLE),
            menu=self._build_menu(),
        )
        self._icon.run_detached()

    def set_state(self, state: State) -> None:
        """Safe to call from the Qt main thread."""
        self._state = state
        if self._icon is None:
            return
        self._icon.icon = _icon_image(_STATE_COLORS[state])
        self._icon.title = self._title_for_state(state)

    def stop(self) -> None:
        if self._icon is not None:
            self._icon.stop()
            self._icon = None
