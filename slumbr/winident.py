"""Windows app identity — the AppUserModelID (AUMID).

Slumbr runs as ``pythonw.exe -m slumbr`` in source mode, so without an explicit
AUMID Windows identifies it as "Python": pinning it pins Python, and the running
window groups under a separate "Python" taskbar button instead of Slumbr.

Setting one stable AUMID — here on the process, and the SAME string on the
shortcuts (see ``scripts/install_shortcut.py``) — makes Windows treat all of it
as a single app, "Slumbr", across the taskbar, pinning, jump list, and Start.
Nothing Python should ever shine through.
"""

from __future__ import annotations

import sys

# Form: CompanyName.ProductName (Microsoft's AUMID convention). Keep this in
# lockstep with the value stamped on the shortcuts.
APP_USER_MODEL_ID = "SleepyDev.Slumbr"


def set_process_app_id() -> None:
    """Tag this process with Slumbr's AUMID. Call once at startup, BEFORE any
    window is created. Windows-only; best-effort (never blocks launch)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            ctypes.c_wchar_p(APP_USER_MODEL_ID)
        )
    except Exception:
        pass
