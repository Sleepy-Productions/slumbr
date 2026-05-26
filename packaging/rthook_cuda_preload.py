# PyInstaller runtime hook — preload CUDA + CTranslate2 BEFORE PySide6.
#
# Why this exists: PyInstaller's PySide6 runtime hook (pyi_rth_pyside6) calls
# _pyi_rth_utils.qt.create_embedded_qt_conf(), which does
# `importlib.import_module("PySide6.QtCore")` during the runtime-hook phase —
# i.e. BEFORE the entry script (slumbr_entry.py) ever runs. Importing PySide6
# perturbs the Windows DLL search path such that CTranslate2's later CUDA model
# construction access-violates (faults in MSVCP140, exit c0000005). Preloading
# ctranslate2 inside slumbr/__init__.py is therefore too late in the frozen
# build — Qt is already loaded by the time our package imports.
#
# Importing `slumbr` here runs its bootstrap (slumbr/__init__: _add_nvidia_dll_dirs
# + _preload_cuda → WinDLL the cuBLAS/cuDNN DLLs by absolute path + import
# ctranslate2 + init the CUDA runtime), pinning the whole CUDA stack into the
# process FIRST. As long as this runtime hook runs before pyi_rth_pyside6, the
# order matches the known-good path (ctranslate2 before PySide6) and the crash
# is avoided.
#
# Best-effort: a failure here must not stop startup — the engine's own error
# path will surface anything actionable with user-readable context.
try:
    import slumbr  # noqa: F401  — side effect: CUDA/CTranslate2 preload
except Exception:
    pass
