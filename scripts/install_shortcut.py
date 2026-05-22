"""Create a Slumbr desktop shortcut (.lnk) on Windows.

Run once after cloning + venv setup:
    .\\.venv\\Scripts\\python.exe scripts\\install_shortcut.py

The shortcut launches `pythonw.exe -m slumbr` (no console window) from
the project venv with the working directory set to the repo root, and
uses `slumbr/assets/icon.ico` for the icon. Re-running overwrites the
existing shortcut so palette/icon changes propagate.

No external dependencies — uses the COM interface to Windows Script Host
which ships with every Windows install. If `pywin32`/`win32com.client`
isn't available we fall back to a plain VBScript file the user runs once.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHONW = ROOT / ".venv" / "Scripts" / "pythonw.exe"
ICON_PATH = ROOT / "slumbr" / "assets" / "icon.ico"
SHORTCUT_NAME = "Slumbr.lnk"


def _desktop_path() -> Path:
    # Honor OneDrive-redirected Desktop folders by reading USERPROFILE +
    # the localized "Desktop" name from the registry would be ideal — but
    # OneDrive sync also copies USERPROFILE\Desktop, so this works for
    # ~all home setups in practice.
    profile = os.environ.get("USERPROFILE")
    if not profile:
        raise SystemExit("USERPROFILE not set — can't find Desktop")
    onedrive_desktop = Path(profile) / "OneDrive" / "Desktop"
    plain_desktop = Path(profile) / "Desktop"
    if onedrive_desktop.is_dir():
        return onedrive_desktop
    return plain_desktop


def _check_prereqs() -> None:
    missing: list[str] = []
    if not VENV_PYTHONW.is_file():
        missing.append(f"missing pythonw: {VENV_PYTHONW}")
    if not ICON_PATH.is_file():
        missing.append(
            f"missing icon: {ICON_PATH} — run scripts/build_icon.py first"
        )
    if missing:
        for m in missing:
            print(f"  - {m}")
        raise SystemExit("prerequisites missing")


def _via_pywin32(out_path: Path) -> bool:
    try:
        from win32com.client import Dispatch  # type: ignore[import-not-found]
    except ImportError:
        return False
    shell = Dispatch("WScript.Shell")
    sc = shell.CreateShortcut(str(out_path))
    sc.TargetPath = str(VENV_PYTHONW)
    sc.Arguments = "-m slumbr"
    sc.WorkingDirectory = str(ROOT)
    sc.IconLocation = str(ICON_PATH)
    sc.WindowStyle = 7  # minimized — pythonw has no window anyway, this just
    # avoids a transient flash on slow machines
    sc.Description = "Slumbr — local voice-to-text dictation"
    sc.Save()
    return True


def _via_vbscript(out_path: Path) -> None:
    """Fallback for venvs that don't have pywin32 installed."""
    vbs_path = ROOT / "scripts" / "_install_shortcut.vbs"
    vbs = f"""Set ws = WScript.CreateObject("WScript.Shell")
Set sc = ws.CreateShortcut("{out_path}")
sc.TargetPath = "{VENV_PYTHONW}"
sc.Arguments = "-m slumbr"
sc.WorkingDirectory = "{ROOT}"
sc.IconLocation = "{ICON_PATH}"
sc.WindowStyle = 7
sc.Description = "Slumbr - local voice-to-text dictation"
sc.Save
"""
    vbs_path.write_text(vbs, encoding="utf-8")
    print(
        "pywin32 not available — writing a VBScript fallback. "
        f"Double-click {vbs_path} once to create the shortcut, then delete "
        "the .vbs file. (Or `pip install pywin32` and rerun this script.)"
    )


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("Slumbr is Windows-only; shortcut script is a no-op elsewhere")
    _check_prereqs()
    desktop = _desktop_path()
    if not desktop.is_dir():
        raise SystemExit(f"Desktop folder not found at {desktop}")
    out_path = desktop / SHORTCUT_NAME
    if _via_pywin32(out_path):
        print(f"created {out_path}")
        return
    _via_vbscript(out_path)


if __name__ == "__main__":
    main()
