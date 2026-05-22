"""Slumbr — local voice-to-text dictation runner.

This module's import-time side effect adds the NVIDIA pip-wheel DLL
directories to the Windows DLL search path, so CTranslate2 can find
`cublas64_12.dll`, `cudnn64_9.dll`, and `nvrtc64_120_0.dll` at runtime.

The pip wheels (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`,
`nvidia-cuda-nvrtc-cu12`) drop the DLLs into
`site-packages/nvidia/<lib>/bin/`, which is NOT on Windows's default DLL
search path. Without this bootstrap, the first transcription raises
`Library cublas64_12.dll is not found or cannot be loaded`.

Must run before `import faster_whisper` (which transitively imports
ctranslate2). Putting it here ensures any `from slumbr...` import wires
the DLL paths first.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

__version__ = "0.1.0"


def _configure_logging() -> None:
    """One-time root-logger setup.

    Two sinks:
      - Console (stdout): INFO+; what you see when you run `python -m slumbr`
        from a terminal. ``--debug`` in ``__main__`` flips Slumbr loggers to
        DEBUG for the console sink only.
      - Rotating file at ``%APPDATA%\\Slumbr\\logs\\slumbr.log``: DEBUG+, captures
        everything regardless of console verbosity. Survives across runs and
        across crashes. ~5 MB per file × 5 backups (25 MB cap) so it can't grow
        unbounded. This is the load-bearing sink when Slumbr launches via the
        desktop shortcut through ``pythonw.exe``, because pythonw discards stdout.

    Chatty third-party libs are pinned to WARNING so the log stream stays
    readable instead of being drowned in httpcore / PIL plugin-import / urllib3.

    Note: the file log captures every transcript verbatim. Fine on a personal
    machine; worth knowing if a copy of the log file ever needs to leave it.
    """
    root = logging.getLogger()
    if root.handlers:
        return

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # ----- console
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.setLevel(logging.INFO)
    root.addHandler(console)

    # ----- rotating file
    try:
        from logging.handlers import RotatingFileHandler  # noqa: PLC0415

        appdata = os.environ.get("APPDATA")
        log_dir = (
            (Path(appdata) / "Slumbr" / "logs")
            if appdata
            else (Path.home() / ".slumbr" / "logs")
        )
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "slumbr.log"
        # Use the same timestamp format as the console but with a date so a
        # multi-day log is navigable.
        file_formatter = logging.Formatter(
            fmt="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(file_formatter)
        file_handler.setLevel(logging.DEBUG)
        root.addHandler(file_handler)
        # Root must be at DEBUG for the file sink to receive DEBUG records,
        # but per-logger levels still gate what gets through — third-party
        # noise levels below filter it out for both sinks.
        root.setLevel(logging.DEBUG)
    except Exception:  # noqa: BLE001
        # File sink is best-effort. If %APPDATA% is denied or the disk is
        # full, console-only is still better than crashing on startup.
        root.setLevel(logging.INFO)

    for noisy in (
        "httpcore",
        "httpx",
        "urllib3",
        "PIL",
        "PIL.Image",
        "PIL.PngImagePlugin",
        "huggingface_hub",
        "filelock",
        "asyncio",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)


_configure_logging()


def _add_nvidia_dll_dirs() -> None:
    if sys.platform != "win32":
        return
    nvidia_root = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    if not nvidia_root.is_dir():
        return
    # CTranslate2's binding uses bare LoadLibrary, which honors PATH but not
    # add_dll_directory. We do both — PATH is the load-bearing one, but
    # add_dll_directory keeps the search scoped for any caller that uses the
    # newer Windows API.
    bin_dirs: list[str] = []
    for sub in ("cublas", "cudnn", "cuda_nvrtc", "cuda_runtime"):
        bin_dir = nvidia_root / sub / "bin"
        if bin_dir.is_dir():
            bin_dirs.append(str(bin_dir))
            try:
                os.add_dll_directory(str(bin_dir))
            except OSError:
                pass
    if bin_dirs:
        os.environ["PATH"] = os.pathsep.join(bin_dirs) + os.pathsep + os.environ.get("PATH", "")


def _preload_ctranslate2() -> None:
    """Force CTranslate2's native module to load before anything else.

    Specifically, this MUST happen before PySide6 is imported. PySide6's
    Windows bootstrap perturbs the DLL search path in a way that breaks
    CTranslate2's later CUDA DLL resolution: the first `WhisperModel(...)`
    constructed after `import PySide6` crashes the process natively
    (exit 5, no Python traceback). Importing `ctranslate2` here resolves
    cuBLAS/cuDNN into the process up front, so by the time `slumbr.app`
    imports PySide6, the CUDA DLLs are already mapped and immune to Qt's
    interference.

    A failed import is non-fatal — the eventual error will surface from
    `WhisperEngine` instead, where it has user-readable context.
    """
    if sys.platform != "win32":
        return
    try:
        import ctranslate2  # noqa: F401
    except Exception:  # noqa: BLE001
        pass


_add_nvidia_dll_dirs()
_preload_ctranslate2()
