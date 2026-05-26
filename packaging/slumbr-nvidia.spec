# PyInstaller spec — NVIDIA Slumbr (faster-whisper on CUDA via CTranslate2).
#
# Build from a venv that has the [cuda] extra installed (the dev .venv does):
#     pyinstaller packaging/slumbr-nvidia.spec --noconfirm
#
# Bigger than the CPU build (~1.5-2 GB onedir) because it bundles the CUDA
# runtime DLLs. The AMD/DirectML stack (torch/transformers/optimum) is still
# excluded — that flavor is best left to pip-on-demand (see README).
#
# NOTE: NOT yet built/verified end-to-end like the CPU flavor. The likely
# iteration point is CUDA DLL resolution inside the frozen bundle — handled
# by collecting the nvidia.* wheels below + the _MEIPASS branch added to
# slumbr/__init__._add_nvidia_dll_dirs. Verify the first transcribe loads
# cublas/cudnn on a clean machine.

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas: list = []
binaries: list = []
hiddenimports: list = []

# Engine + model-download stack, plus the CUDA runtime wheels (these ship the
# cublas/cudnn/nvrtc DLLs CTranslate2 loads at runtime).
for _pkg in (
    "sherpa_onnx", "ctranslate2", "faster_whisper", "huggingface_hub", "tokenizers",
    "nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_nvrtc", "nvidia.cuda_runtime",
):
    try:
        _d, _b, _h = collect_all(_pkg)
        datas += _d
        binaries += _b
        hiddenimports += _h
    except Exception:  # nvidia.* subpackage not present → skip
        pass

hiddenimports += collect_submodules("slumbr")
datas += [("../slumbr/assets", "slumbr/assets")]
hiddenimports += [
    "slumbr.stt.backends.whisper_ct2",
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
        # AMD/DirectML stack only — keep CUDA.
        "torch", "torchaudio", "transformers", "optimum", "onnxruntime_directml",
        "tkinter", "matplotlib", "pandas", "scipy", "pytest", "IPython",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Slumbr", console=False,
          icon="../slumbr/assets/icon.ico")
coll = COLLECT(exe, a.binaries, a.datas, name="Slumbr")
