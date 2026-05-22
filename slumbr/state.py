from __future__ import annotations

import threading
import time
from collections.abc import Callable
from enum import Enum


class State(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    PASTING = "pasting"


class StateMachine:
    """Thread-safe state holder with a 200 ms debounce between transitions.

    Higher-level orchestrator decides which transitions are valid.
    `try_transition()` returns False if the debounce window blocks the move,
    so a double-tap of the hotkey doesn't whipsaw the state.
    """

    DEBOUNCE_MS = 200

    def __init__(self, on_change: Callable[[State, State], None] | None = None) -> None:
        self._state = State.IDLE
        self._lock = threading.Lock()
        self._last_transition_ms = 0.0
        self._on_change = on_change

    @property
    def state(self) -> State:
        with self._lock:
            return self._state

    def try_transition(self, to: State, *, force: bool = False) -> bool:
        """Move to `to` unless blocked by the user-debounce window.

        `force=True` bypasses the debounce — use it for automatic transitions
        the orchestrator drives (e.g. PASTING -> IDLE on completion). The
        debounce exists to absorb hotkey double-taps, not to slow down the
        state machine's own forward progress.
        """
        with self._lock:
            now_ms = time.monotonic() * 1000.0
            if not force and now_ms - self._last_transition_ms < self.DEBOUNCE_MS:
                return False
            prev = self._state
            self._state = to
            self._last_transition_ms = now_ms
        if self._on_change is not None:
            self._on_change(prev, to)
        return True
