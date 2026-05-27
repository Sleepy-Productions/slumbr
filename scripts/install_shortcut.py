"""Create Slumbr shortcuts (.lnk) on Windows — desktop + Start Menu.

Run once after cloning + venv setup:
    .\\.venv\\Scripts\\python.exe scripts\\install_shortcut.py

Each shortcut launches `pythonw.exe -m slumbr` (no console window) from the
project venv, uses `slumbr/assets/icon.ico`, AND carries Slumbr's
AppUserModelID (see slumbr/winident.py). That AUMID is what makes Windows treat
the app as "Slumbr" everywhere — taskbar button, pinning, jump list, Start —
instead of letting the host "Python" shine through. The Start Menu copy also
makes Slumbr findable in Start search and gives Windows a shortcut with the
matching AUMID to resolve when you pin.

Stamping the AUMID needs IPropertyStore, so we build the link through the shell
IShellLink COM interface (pywin32). If pywin32 is somehow missing we fall back
to a plain WScript.Shell shortcut WITHOUT the AUMID — install still yields a
working launcher; the pin will just read as Python until pywin32 is present.
(install.ps1 pip-installs pywin32 before calling this, so the fallback is rare.)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from slumbr.winident import APP_USER_MODEL_ID

ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHONW = ROOT / ".venv" / "Scripts" / "pythonw.exe"
VENV_SLUMBR = ROOT / ".venv" / "Scripts" / "Slumbr.exe"
FROZEN_EXE = ROOT / "dist" / "Slumbr" / "Slumbr.exe"
# The STABLE, shipped install (Inno → {autopf}\Slumbr, which for a per-user
# install resolves to %LOCALAPPDATA%\Programs\Slumbr). A pinned shortcut should
# point HERE, not at the volatile dist/ build dir — that's what makes it
# "persistent": it survives `pyinstaller` rebuilds (which wipe dist/), exactly
# like YT Grab points its shortcut at %LOCALAPPDATA%\Programs\YTGrab.
_LOCALAPPDATA = os.environ.get("LOCALAPPDATA")
INSTALLED_EXE = (
    Path(_LOCALAPPDATA) / "Programs" / "Slumbr" / "Slumbr.exe" if _LOCALAPPDATA else None
)
ICON_PATH = ROOT / "slumbr" / "assets" / "icon.ico"
SHORTCUT_NAME = "Slumbr.lnk"
_DESCRIPTION = "Slumbr — local voice-to-text dictation"
_SW_SHOWMINNOACTIVE = 7  # pythonw has no window; this just avoids a transient flash


def _target() -> tuple[Path, str, Path]:
    """What the shortcut launches, as (target, arguments, working_dir).

    Precedence (most-stable first):
      1. INSTALLED build (%LOCALAPPDATA%\\Programs\\Slumbr\\Slumbr.exe) — the
         shipped copy. Point here so the shortcut is PERSISTENT: it keeps
         working across `dist/` rebuilds (PyInstaller wipes dist/ each build).
         This mirrors YT Grab, whose shortcut targets its installed Programs copy.
      2. FROZEN dist build (dist/Slumbr/Slumbr.exe) — exists right after a build
         but before an Inno install; volatile, so only a fallback.
      3. venv launcher running `-m slumbr` — pure source/dev install.
    Cases 1-2 are real self-owned processes that own their window, so the
    taskbar/pin read "Slumbr" with the brand icon. Case 3 pins as Python
    (a pythonw/venv-redirector limitation; brand_launcher.py mitigates the icon).
    No arguments for the exe cases (the exe IS the app); working dir is its own
    folder so the onedir _internal/ resolves."""
    if INSTALLED_EXE is not None and INSTALLED_EXE.is_file():
        return INSTALLED_EXE, "", INSTALLED_EXE.parent
    if FROZEN_EXE.is_file():
        return FROZEN_EXE, "", FROZEN_EXE.parent
    launcher = VENV_SLUMBR if VENV_SLUMBR.is_file() else VENV_PYTHONW
    return launcher, "-m slumbr", ROOT


def _desktop_path() -> Path:
    # Honor OneDrive-redirected Desktop folders — OneDrive sync also copies
    # USERPROFILE\Desktop, so this works for ~all home setups in practice.
    profile = os.environ.get("USERPROFILE")
    if not profile:
        raise SystemExit("USERPROFILE not set — can't find Desktop")
    onedrive_desktop = Path(profile) / "OneDrive" / "Desktop"
    plain_desktop = Path(profile) / "Desktop"
    return onedrive_desktop if onedrive_desktop.is_dir() else plain_desktop


def _start_menu_dir() -> Path | None:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def _check_prereqs() -> None:
    missing: list[str] = []
    have_launcher = (
        (INSTALLED_EXE is not None and INSTALLED_EXE.is_file())
        or FROZEN_EXE.is_file()
        or VENV_PYTHONW.is_file()
    )
    if not have_launcher:
        missing.append(f"no launcher: none of {INSTALLED_EXE}, {FROZEN_EXE}, {VENV_PYTHONW}")
    if not ICON_PATH.is_file():
        missing.append(f"missing icon: {ICON_PATH} — run scripts/build_icon.py first")
    if missing:
        for m in missing:
            print(f"  - {m}")
        raise SystemExit("prerequisites missing")


def _make_shortcut(out_path: Path) -> bool:
    """Build a .lnk with the Slumbr AUMID via IShellLink + IPropertyStore.
    Returns False if pywin32's shell/propsys modules aren't importable."""
    try:
        import pythoncom
        from win32com.propsys import propsys, pscon
        from win32com.shell import shell  # type: ignore[import-not-found]
    except ImportError:
        return False

    link = pythoncom.CoCreateInstance(
        shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IShellLink
    )
    target, args, workdir = _target()
    link.SetPath(str(target))
    link.SetArguments(args)
    link.SetWorkingDirectory(str(workdir))
    # When the target is a branded Slumbr.exe (installed/frozen build, or the
    # brand_launcher'd venv copy) it embeds the brand icon at index 0 — use that
    # so the shortcut is self-contained and never depends on the repo's assets
    # path. Only the bare pythonw fallback needs the separate icon.ico.
    icon_src = str(target) if target.name.lower() == "slumbr.exe" else str(ICON_PATH)
    link.SetIconLocation(icon_src, 0)
    link.SetDescription(_DESCRIPTION)
    link.SetShowCmd(_SW_SHOWMINNOACTIVE)

    store = link.QueryInterface(propsys.IID_IPropertyStore)
    store.SetValue(
        pscon.PKEY_AppUserModel_ID,
        propsys.PROPVARIANTType(APP_USER_MODEL_ID, pythoncom.VT_LPWSTR),
    )
    store.Commit()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    link.QueryInterface(pythoncom.IID_IPersistFile).Save(str(out_path), 0)
    return True


def _via_vbscript(out_path: Path) -> None:
    """Last-resort fallback for venvs without pywin32. Produces a working
    launcher but cannot set the AUMID (pin will read as Python until pywin32
    is installed and this script is re-run)."""
    vbs_path = ROOT / "scripts" / "_install_shortcut.vbs"
    _target_exe, _args, _workdir = _target()
    vbs = f"""Set ws = WScript.CreateObject("WScript.Shell")
Set sc = ws.CreateShortcut("{out_path}")
sc.TargetPath = "{_target_exe}"
sc.Arguments = "{_args}"
sc.WorkingDirectory = "{_workdir}"
sc.IconLocation = "{ICON_PATH}"
sc.WindowStyle = {_SW_SHOWMINNOACTIVE}
sc.Description = "Slumbr - local voice-to-text dictation"
sc.Save
"""
    vbs_path.write_text(vbs, encoding="utf-8")
    print(
        "pywin32 not available — wrote a VBScript fallback (no AUMID). "
        f"Double-click {vbs_path} once to create the shortcut, then delete it. "
        "(Or `pip install pywin32` and rerun this script for proper pinning.)"
    )


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("Slumbr is Windows-only; shortcut script is a no-op elsewhere")
    _check_prereqs()

    targets: list[Path] = []
    desktop = _desktop_path()
    if desktop.is_dir():
        targets.append(desktop / SHORTCUT_NAME)
    start_menu = _start_menu_dir()
    if start_menu is not None:
        targets.append(start_menu / SHORTCUT_NAME)

    if not targets:
        raise SystemExit("found neither a Desktop nor a Start Menu folder to install into")

    for out_path in targets:
        if _make_shortcut(out_path):
            print(f"created {out_path}  (AUMID={APP_USER_MODEL_ID})")
        else:
            # pywin32 missing entirely — one basic desktop shortcut is enough.
            _via_vbscript(desktop / SHORTCUT_NAME)
            return


if __name__ == "__main__":
    main()
