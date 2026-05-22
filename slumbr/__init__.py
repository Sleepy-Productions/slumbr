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
    """One-time root-logger setup. `--debug` flips Slumbr loggers to DEBUG
    in `__main__`; chatty third-party libs are pinned to WARNING below so
    debug mode stays readable instead of being drowned in httpcore / PIL
    plugin-import / urllib3 noise.
    """
    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root.addHandler(handler)
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
