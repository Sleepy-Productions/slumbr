"""Resolve model weights that were bundled into a frozen build.

For a fully-offline first run: the PyInstaller specs copy the Moonshine /
Silero-VAD / online-punct ONNX files (and, in the NVIDIA build, the CT2
Whisper model) into ``<bundle>/models/``. When frozen, the model loaders
check here *before* downloading from Hugging Face, so a fresh machine with
no network still transcribes on first launch.

Returns ``None`` when not frozen or when nothing was bundled, so source /
dev runs keep the original download-on-first-run behavior unchanged.
"""

from __future__ import annotations

import sys
from pathlib import Path


def bundled_models_root() -> Path | None:
    """``<frozen-bundle>/models`` if it exists, else ``None``.

    ``sys._MEIPASS`` is set by PyInstaller for both onefile and onedir
    builds; it points at the bundle root where ``datas`` land. A source
    checkout has no ``_MEIPASS`` and returns ``None`` — callers then fall
    back to their normal Hugging Face download path.
    """
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return None
    root = Path(base) / "models"
    return root if root.is_dir() else None
