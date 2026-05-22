"""Track the most-recent non-system foreground window.

Why this exists: when the user clicks the tray icon's menu to toggle
recording, focus moves from their actual target window (e.g. Notepad)
onto the taskbar / shell. By the time we reach the paste step the
foreground window is wrong, so Ctrl+V goes nowhere useful.

We solve it by polling `GetForegroundWindow` continuously in a daemon
thread and remembering the last hwnd that *isn't* one of: our own
process, the taskbar, the desktop, or a notify-icon overflow popup.
Paste then restores that hwnd before sending Ctrl+V.

Polling at 10 Hz is plenty — focus-change events are user-initiated and
not latency-sensitive. We deliberately avoid `SetWinEventHook` (which
would be event-driven) because it requires a Win32 message loop owned
by the installing thread, complicating shutdown.
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.SetForegroundWindow.argtypes = [wintypes.HWND]
_user32.SetForegroundWindow.restype = wintypes.BOOL
_user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetClassNameW.restype = ctypes.c_int
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD

_OUR_PID = _kernel32.GetCurrentProcessId()

# Window class names representing OS shell surfaces we never want to
# paste into. Sourced from Win11 inspect.exe — extend if we find more.
_IGNORED_CLASSES = frozenset(
    {
        "Shell_TrayWnd",  # main taskbar
        "Shell_SecondaryTrayWnd",  # multi-monitor taskbar
        "Progman",  # desktop
        "WorkerW",  # desktop wallpaper layer
        "NotifyIconOverflowWindow",  # tray overflow popup
        "TopLevelWindowForOverflowXamlIsland",  # Win11 tray overflow
        "Windows.UI.Core.CoreWindow",  # Start menu, system UI surfaces
    }
)


def _class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    _user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _owned_by_us(hwnd: int) -> bool:
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value == _OUR_PID


class ForegroundTracker:
    def __init__(self, poll_hz: float = 10.0) -> None:
        self._interval = 1.0 / poll_hz
        self._last_hwnd: int | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            try:
                hwnd = _user32.GetForegroundWindow()
                if hwnd:
                    cls = _class_name(hwnd)
                    if cls not in _IGNORED_CLASSES and not _owned_by_us(hwnd):
                        with self._lock:
                            self._last_hwnd = hwnd
            except Exception:  # noqa: BLE001
                pass
            self._stop.wait(self._interval)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="fg-tracker")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def last_hwnd(self) -> int | None:
        with self._lock:
            return self._last_hwnd


def restore_foreground(hwnd: int) -> bool:
    """Bring `hwnd` to the foreground. Returns True if Windows accepted."""
    return bool(_user32.SetForegroundWindow(hwnd))


def current_foreground() -> tuple[int, str, str]:
    """Diagnostic helper: returns (hwnd, class_name, title) of the current foreground."""
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return 0, "", ""
    cls = _class_name(hwnd)
    title_buf = ctypes.create_unicode_buffer(256)
    _user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowTextW(hwnd, title_buf, 256)
    return hwnd, cls, title_buf.value
