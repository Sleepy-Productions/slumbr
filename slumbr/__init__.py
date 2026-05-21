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

import os
import sys
from pathlib import Path

__version__ = "0.1.0"


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


_add_nvidia_dll_dirs()
