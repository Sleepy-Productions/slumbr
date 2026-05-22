"""Driven VB-Cable install from inside Slumbr.

What this does:
  1. Downloads the official VB-Audio Virtual Cable driver pack zip
     from vb-audio.com (no bundled binary — we always fetch fresh
     to stay legally clean and to pick up new versions automatically).
  2. Extracts to ``%TEMP%\\slumbr-vbcable-XXXX\\``.
  3. Launches ``VBCABLE_Setup_x64.exe`` (or the 32-bit setup on legacy
     boxes) with ``-Verb RunAs`` so Windows triggers the UAC prompt.
  4. Blocks until the elevated installer exits.
  5. Returns success. The caller prompts the user to reboot before
     Slumbr re-launches — driver loading requires it.

What this does NOT do:
  - It doesn't bundle the .exe (license cleanliness — VB-Cable is
    "Donationware" by Vincent Burel; fetching fresh from his site
    each time keeps redistribution off the table).
  - It doesn't silently install. VB-Cable's setup has no silent
    flag documented as of Pack45 (Oct 2024). The user clicks
    "Install Driver" once inside their installer.
  - It doesn't reboot for the user — the driver won't appear in
    sounddevice's device list until Windows is restarted, and we
    don't want to schedule a reboot under the user's nose.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

# Pinned to the canonical Pack45 URL (Oct 2024 — current as of build).
# VB-Audio occasionally renames the file with a new pack number; if
# this 404s the user can fall back to the manual install link.
DEFAULT_ZIP_URL = "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip"


def install_vbcable(
    zip_url: str = DEFAULT_ZIP_URL,
    on_progress: Callable[[str], None] | None = None,
) -> None:
    """Download → extract → run setup elevated. Blocking.

    Raises on any failure (download error, missing setup, UAC decline,
    installer non-zero exit). Caller catches and surfaces the message.
    """
    progress = on_progress or (lambda _msg: None)

    tmp = Path(tempfile.mkdtemp(prefix="slumbr-vbcable-"))
    try:
        # ----- download
        zip_path = tmp / "VBCABLE_Driver_Pack.zip"
        progress(f"Downloading {zip_url}")
        try:
            urllib.request.urlretrieve(zip_url, zip_path)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                f"download failed ({e}). Check internet, or install manually "
                "from vb-audio.com/Cable."
            ) from e
        size_mb = zip_path.stat().st_size / (1024 * 1024)
        progress(f"Downloaded {size_mb:.1f} MB.")

        # ----- extract
        extract_dir = tmp / "extracted"
        try:
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(extract_dir)
        except zipfile.BadZipFile as e:
            raise RuntimeError(f"downloaded file is not a valid zip: {e}") from e
        progress("Extracted driver pack.")

        # ----- find the right setup exe (prefer 64-bit)
        setup_exe = next(extract_dir.rglob("VBCABLE_Setup_x64.exe"), None)
        if setup_exe is None:
            setup_exe = next(extract_dir.rglob("VBCABLE_Setup.exe"), None)
        if setup_exe is None:
            raise RuntimeError(
                "no VBCABLE_Setup*.exe found in the downloaded zip — "
                "VB-Audio may have changed their packaging."
            )
        progress(f"Launching {setup_exe.name} (Windows will prompt for admin).")

        # ----- elevated launch via PowerShell
        # Start-Process -Verb RunAs triggers UAC. -Wait blocks until
        # the elevated process exits. -WindowStyle Hidden suppresses
        # the brief PowerShell flash.
        ps_command = (
            f"$ErrorActionPreference='Stop'; "
            f"Start-Process -FilePath '{setup_exe}' -Verb RunAs -Wait"
        )
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-WindowStyle", "Hidden",
                "-Command", ps_command,
            ],
            capture_output=True,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            stderr = (result.stderr or result.stdout or "").strip()
            # Common case: UAC decline ("The operation was cancelled by the user.")
            if "cancel" in stderr.lower():
                raise RuntimeError("UAC denied — install cancelled.")
            raise RuntimeError(f"installer launch failed: {stderr or '(no output)'}")
        progress("Installer exited.")
        progress("Reboot Windows when ready, then re-launch Slumbr.")
    finally:
        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------- Qt worker


class VBCableInstallWorker(QObject):
    """Background worker. Move to a QThread, connect signals, call ``run``."""

    progress = Signal(str)
    finished = Signal(bool, str)  # (success, summary)

    def run(self) -> None:
        try:
            install_vbcable(on_progress=self.progress.emit)
            self.finished.emit(True, "VB-Cable installed. Reboot Windows, then re-launch Slumbr.")
        except Exception as e:  # noqa: BLE001
            log.warning("VB-Cable install failed: %s", e)
            self.finished.emit(False, str(e))
