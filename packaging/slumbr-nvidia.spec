# PyInstaller spec — NVIDIA Slumbr (faster-whisper on CUDA via CTranslate2).
#
# Build from a venv that has the [cuda] extra installed (the dev .venv does):
#     pyinstaller packaging/slumbr-nvidia.spec --noconfirm
#
# Bigger than the CPU build (~1.5-2 GB onedir) because it bundles the CUDA
# runtime DLLs. The AMD/DirectML stack (torch/transformers/optimum) is still
# excluded — that flavor is best left to pip-on-demand (see README).
#
# VERIFIED end-to-end (2026-05-26): the frozen exe loads large-v3-turbo on
# CUDA, warm-up transcribes on the GPU, and reaches "ready". The hard part was
# that PyInstaller's PySide6 runtime hook imports QtCore during bootstrap
# (before the entry script), which broke CTranslate2's CUDA init — fixed by
# rthook_cuda_preload.py (runtime_hooks below), which preloads CUDA/CTranslate2
# BEFORE Qt. cuDNN/NVRTC are pruned (see _PRUNE_DLLS) since CTranslate2's
# inference path is cuBLAS-based and never loads them.

import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

# cuDNN sub-libraries + NVRTC that CTranslate2's Whisper inference never loads
# (it's cuBLAS/GEMM-based — proven empirically via EnumProcessModulesEx during a
# real large-v3-turbo int8_float16 transcription: only cublas64_12, cublasLt64_12
# and the tiny cudnn64_9 dispatcher map in). Pruning these from the frozen bundle
# saves ~1.18 GB. cudnn64_9.dll is KEPT — it's a link-time import of
# ctranslate2.dll, but it only lazily loads these heavy sub-libs on a cuDNN call,
# which CTranslate2 never makes for this workload. Filtered off a.binaries AFTER
# Analysis so the nvidia.cudnn collector hook can't re-add them.
_PRUNE_DLLS = {
    "cudnn_adv64_9.dll",
    "cudnn_cnn64_9.dll",
    "cudnn_graph64_9.dll",
    "cudnn_ops64_9.dll",
    "cudnn_heuristic64_9.dll",
    "cudnn_engines_precompiled64_9.dll",
    "cudnn_engines_runtime_compiled64_9.dll",
    "cudnn_engines_tensor_ir64_9.dll",
    "cudnn_ext64_9.dll",
    "nvrtc64_120_0.dll",
    "nvrtc64_120_0.alt.dll",
    "nvrtc-builtins64_129.dll",
    "nvblas64_12.dll",
}

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

# Bundled model weights → instant-offline popup partials + CPU fallback. Staged
# into packaging/bundled-models/ (gitignored). Each lands at <bundle>/models/
# <name>/ where slumbr/_bundled.py resolves it before any Hugging Face download.
# The NVIDIA build bundles ONLY the Moonshine/VAD/punct trio that always drives
# the popup partials + the CPU fallback engine — NOT the 1.5 GB CT2 Whisper
# turbo, which would push the installer past GitHub's 2 GiB release-asset cap.
# The GPU primary model downloads once on first launch (progress dialog in
# ui/preparing.py); the CPU build stays fully offline.
_BUNDLED = os.path.join(SPECPATH, "bundled-models")
for _sub in ("moonshine-base-en", "silero-vad", "online-punct-en"):
    _msrc = os.path.join(_BUNDLED, _sub)
    if os.path.isdir(_msrc):
        datas += [(_msrc, "models/" + _sub)]
    else:
        raise SystemExit(f"[slumbr] missing bundled model dir: {_msrc} — stage it before building")

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
    # MUST preload CTranslate2/CUDA before PyInstaller's PySide6 runtime hook
    # imports QtCore (which otherwise breaks CUDA DLL resolution → access
    # violation). See rthook_cuda_preload.py. (runtime_hooks resolve relative to
    # the build CWD, not the spec dir, so anchor on SPECPATH.)
    runtime_hooks=[os.path.join(SPECPATH, "rthook_cuda_preload.py")],
    noarchive=False,
)
# Drop the unused cuDNN sub-libs + NVRTC (see _PRUNE_DLLS). Runs after Analysis
# so it also strips what the nvidia.cudnn / nvidia.cuda_nvrtc hooks collected.
_before = len(a.binaries)
a.binaries = [
    b for b in a.binaries if os.path.basename(b[0]).lower() not in _PRUNE_DLLS
]
print(f"[slumbr] pruned {_before - len(a.binaries)} CUDA DLLs from the bundle")
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Slumbr", console=False,
          icon="../slumbr/assets/icon.ico",
          version=os.path.join(SPECPATH, "version_info.txt"))
coll = COLLECT(exe, a.binaries, a.datas, name="Slumbr")
