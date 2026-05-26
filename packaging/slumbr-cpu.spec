# PyInstaller spec — CPU-only Slumbr (Moonshine + faster-whisper on CPU).
#
# Build (ideally from a fresh CPU venv — see packaging/README.md):
#     pyinstaller packaging/slumbr-cpu.spec --noconfirm
# Produces dist/Slumbr/ (a portable onedir). packaging/slumbr.iss wraps it
# into a Windows installer.
#
# CPU-only on purpose: the GPU (CUDA) and AMD/Intel (DirectML + torch +
# transformers) stacks are excluded so the bundle stays ~150-300 MB instead
# of 4-5 GB. faster-whisper falls back to CPU when the CUDA libs are absent.

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas: list = []
binaries: list = []
hiddenimports: list = []

# Native-data packages PyInstaller's analysis misses (bundled .dll / .onnx),
# plus huggingface_hub — it's imported lazily inside _ensure_moonshine (model
# download), so static analysis never sees it and it gets left out otherwise.
for _pkg in ("sherpa_onnx", "ctranslate2", "faster_whisper", "huggingface_hub", "tokenizers"):
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h

# Slumbr's own package + its assets (tray/popup icon).
hiddenimports += collect_submodules("slumbr")
datas += [("../slumbr/assets", "slumbr/assets")]

# The factory imports backends lazily (`from .backends.X import …` inside
# functions), so PyInstaller's static analysis can't see them — name them
# explicitly or the app crashes at backend build time.
hiddenimports += [
    "slumbr.stt.backends.whisper_ct2",   # cuda_ct2 + cpu_ct2
    "slumbr.stt.backends.moonshine",
    "slumbr.stt.backends.directml",
    "slumbr.stt.backends.whispercpp",
]

a = Analysis(
    ["slumbr_entry.py"],
    pathex=[".."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=[
        # GPU / AMD stacks — not used by the CPU build, huge if bundled.
        "torch", "torchaudio", "transformers", "optimum",
        "onnxruntime_directml", "nvidia",
        # Dev / notebook junk that sometimes gets pulled transitively.
        "tkinter", "matplotlib", "pandas", "scipy", "pytest", "IPython",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Slumbr",
    console=False,  # no console window (matches pythonw launch)
    icon="../slumbr/assets/icon.ico",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="Slumbr",
)
