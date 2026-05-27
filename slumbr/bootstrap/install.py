"""Subprocess pip driver — installs backend extras into the live venv.

Used by the wizard's Install screen. The contract:

- ``InstallWorker`` is a ``QObject`` that runs ``pip install -e .[<extra>]``
  in a subprocess. It streams stdout + stderr line-by-line as
  ``line(str)`` signals and emits ``finished(success: bool, msg: str)``
  on exit.
- The subprocess uses the current ``sys.executable`` so it lands in the
  same venv slumbr is running in. The repo root is computed from
  ``slumbr.__file__`` (works for editable installs; falls back to a
  PyPI-style ``pip install slumbr[<extra>]`` if not editable).
- ``cancel()`` terminates the subprocess. The user can cancel mid-install
  without leaving a half-installed venv (pip rolls back its own staging
  on SIGTERM; worst case the user retries).

Windows DLL-in-use caveat (load-bearing):
  Some backend wheels include DLLs (``onnxruntime_providers_dml.dll``,
  ``ctranslate2.dll``) that Windows refuses to overwrite while the
  current process has them mapped. So even on a successful pip install,
  the wizard MUST relaunch Slumbr afterwards — pip's "Successfully
  installed" output is the trigger, not "transcribe works now". We
  expose this via ``InstallResult.relaunch_required = True`` so the
  caller doesn't have to scrape the output.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)


@dataclass
class InstallResult:
    success: bool
    summary: str
    relaunch_required: bool = True


def _editable_root() -> Path | None:
    """Return the directory containing pyproject.toml, if Slumbr is
    editable-installed. Returns None for wheel-installed slumbr, in
    which case the caller falls back to a PyPI install target.
    """
    try:
        import slumbr  # noqa: PLC0415
    except ImportError:
        return None
    pkg_dir = Path(slumbr.__file__).resolve().parent
    candidate = pkg_dir.parent  # repo root in editable layout
    if (candidate / "pyproject.toml").is_file():
        return candidate
    return None


def _install_target(extras: list[str]) -> str:
    """Build the ``pip install`` target string.

    Examples:
      - editable + extras → ``"-e .[nvidia]"`` (returned as two args)
      - wheel + extras    → ``"slumbr[nvidia]"``
    """
    extras_token = f"[{','.join(extras)}]" if extras else ""
    root = _editable_root()
    if root is not None:
        return f"-e {root}{extras_token}"
    return f"slumbr{extras_token}"


class InstallWorker(QObject):
    """Background worker. Move to a ``QThread`` and call ``run()``."""

    line = Signal(str)
    finished = Signal(object)  # InstallResult

    def __init__(self, extras: list[str]) -> None:
        super().__init__()
        self._extras = list(extras)
        self._proc: subprocess.Popen[str] | None = None
        self._cancelled = False
        self._lock = threading.Lock()

    def run(self) -> None:
        target = _install_target(self._extras)
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade"] + target.split()
        # `--upgrade` is important: if the user re-runs the wizard to
        # switch from cpu→nvidia mid-life, we want the new wheels even
        # if a stale version of slumbr is already installed.
        log.info("install subprocess: %s", " ".join(cmd))
        self.line.emit(f"$ {' '.join(cmd)}\n")

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as e:
            self.finished.emit(InstallResult(success=False, summary=f"could not start pip: {e}"))
            return

        assert self._proc.stdout is not None
        for raw in iter(self._proc.stdout.readline, ""):
            if not raw:
                break
            self.line.emit(raw.rstrip("\n"))
            with self._lock:
                if self._cancelled:
                    break

        rc = self._proc.wait()
        if self._cancelled:
            self.finished.emit(
                InstallResult(
                    success=False,
                    summary="Install cancelled.",
                    relaunch_required=False,
                )
            )
            return
        if rc == 0:
            self.finished.emit(
                InstallResult(
                    success=True,
                    summary=f"Installed slumbr[{','.join(self._extras)}].",
                )
            )
        else:
            self.finished.emit(
                InstallResult(
                    success=False,
                    summary=f"pip exited with code {rc}. See log above.",
                    relaunch_required=False,
                )
            )

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except OSError:
                pass


def relaunch_slumbr() -> None:
    """Spawn a fresh ``python -m slumbr`` in a detached subprocess and
    return. The caller should immediately exit the current process so
    Windows can release the in-use DLLs from the just-installed wheels.

    Detached spawn matters: if we just `subprocess.Popen(...)` and exit,
    the child inherits stdio handles + becomes a zombie under some
    shells. ``creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP``
    cuts the cord cleanly.
    """
    flags = 0
    if sys.platform == "win32":
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(
            [sys.executable, "-m", "slumbr"],
            creationflags=flags,
            close_fds=True,
        )
    except OSError as e:
        log.warning("could not relaunch slumbr: %s", e)
