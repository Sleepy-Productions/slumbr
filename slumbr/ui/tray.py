"""System-tray icon — Slumbr's only persistent UI chrome.

Provides:
- A violet-toned dot icon whose color reflects the current state
  (gray = idle, primary violet = recording, deep violet = transcribing/pasting).
- A right-click menu: ``Last: …`` (non-clickable header), Toggle Recording,
  Settings, Quit.

There is intentionally no "Show Slumbr" entry — the May 2026 rearch
deleted the hub window and the only places left to interact with Slumbr
are the popup (during dictation) and the Settings dialog (right-click →
Settings…).

pystray runs its own event loop in a dedicated thread (``run_detached``).
Menu callbacks fire on the pystray thread — *do not* touch Qt widgets
from them. The app wires them through a Qt signal so they land on the
main thread.
"""

from __future__ import annotations

from collections.abc import Callable

import pystray
from PIL import Image, ImageDraw

from .. import history
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

_LAST_TRANSCRIPT_MAX = 60


def _icon_image(color: str) -> Image.Image:
    img = Image.new("RGBA", (_ICON_SIZE, _ICON_SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    halo = tuple(int(color[i : i + 2], 16) for i in (1, 3, 5)) + (90,)
    d.ellipse((0, 0, _ICON_SIZE, _ICON_SIZE), fill=halo)
    d.ellipse((6, 6, _ICON_SIZE - 6, _ICON_SIZE - 6), fill=color)
    return img


def _last_transcript_label() -> str:
    """Compact 'Last: …' for the tray header. Called lazily by pystray
    each time the menu opens, so it always reflects the freshest entry.
    """
    text = history.latest()
    if not text:
        return "Last: —"
    snippet = text.replace("\n", " ").strip()
    if len(snippet) > _LAST_TRANSCRIPT_MAX:
        snippet = snippet[: _LAST_TRANSCRIPT_MAX - 1] + "…"
    return f"Last: {snippet}"


class SlumbrTray:
    def __init__(
        self,
        on_toggle: Callable[[], None],
        on_settings: Callable[[], None],
        on_quit: Callable[[], None],
        on_restart: Callable[[], None],
        hotkey_label: str = "Caps Lock",
    ) -> None:
        self._on_toggle = on_toggle
        self._on_settings = on_settings
        self._on_quit = on_quit
        self._on_restart = on_restart
        self._icon: pystray.Icon | None = None
        self._state = State.IDLE
        self._hotkey_label = hotkey_label

    def set_hotkey_label(self, label: str) -> None:
        self._hotkey_label = label
        if self._icon is not None:
            self._icon.title = self._title_for_state(self._state)

    def _title_for_state(self, state: State) -> str:
        if state is State.IDLE:
            return f"Slumbr — Idle (tap {self._hotkey_label} to dictate)"
        return f"Slumbr — {state.value.capitalize()}"

    def _build_menu(self) -> pystray.Menu:
        # The 'Last: …' header is enabled=False so it renders greyed-out
        # and doesn't fire on click. pystray re-invokes the lambda each
        # time the menu opens, so the snippet stays fresh.
        return pystray.Menu(
            pystray.MenuItem(
                lambda _item: _last_transcript_label(),
                None,
                enabled=False,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Toggle Recording",
                lambda _icon, _item: self._on_toggle(),
                default=True,
            ),
            pystray.MenuItem(
                "Settings…",
                lambda _icon, _item: self._on_settings(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Restart Slumbr", lambda _icon, _item: self._on_restart()
            ),
            pystray.MenuItem(
                "Quit Slumbr", lambda _icon, _item: self._on_quit()
            ),
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

    def refresh_menu(self) -> None:
        """Force pystray to repaint the menu (e.g. after a fresh transcript
        so the 'Last:' header reflects it without waiting for the user to
        reopen the menu).
        """
        if self._icon is not None:
            self._icon.update_menu()

    def stop(self) -> None:
        if self._icon is not None:
            self._icon.stop()
            self._icon = None
