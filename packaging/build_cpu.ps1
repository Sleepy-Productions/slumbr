# Build the CPU-only Slumbr installer.
#
#   pwsh packaging\build_cpu.ps1
#
# Builds a fresh CPU venv (so the bundle doesn't drag in CUDA/DirectML/torch),
# PyInstaller-packages it into dist\Slumbr\, then — if Inno Setup is present —
# compiles dist-installer\slumbr-setup-cpu.exe.
#
# Run from the repo root. Use -UseCurrentVenv to skip the clean-venv step and
# build from the active environment (faster, but larger if it has GPU libs).
param(
    [switch]$UseCurrentVenv,
    [switch]$SkipInstaller
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not $UseCurrentVenv) {
    Write-Host "==> Creating a clean CPU build venv (.venv-cpu)..."
    if (Test-Path .venv-cpu) { Remove-Item -Recurse -Force .venv-cpu }
    python -m venv .venv-cpu
    $py = ".\.venv-cpu\Scripts\python.exe"
    & $py -m pip install --upgrade pip
    # Base deps (sherpa-onnx Moonshine + huggingface_hub + Qt). Then add
    # faster-whisper for the cpu_ct2 backend WITHOUT the nvidia-* CUDA wheels
    # (those live in the [cuda] extra) so the bundle stays CPU-sized, plus CPU
    # onnxruntime so we never pull the DirectML build.
    & $py -m pip install .
    & $py -m pip install "faster-whisper==1.2.1"
    & $py -m pip install onnxruntime    # CPU provider, not -directml
    & $py -m pip install pyinstaller
} else {
    $py = ".\.venv\Scripts\python.exe"
    & $py -m pip install pyinstaller
}

Write-Host "==> Cleaning previous build..."
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue

Write-Host "==> Running PyInstaller..."
& $py -m PyInstaller packaging\slumbr-cpu.spec --noconfirm
Write-Host "==> Portable onedir built: dist\Slumbr\  (run dist\Slumbr\Slumbr.exe to test)"

if ($SkipInstaller) { return }
$iscc = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if (Test-Path $iscc) {
    Write-Host "==> Compiling installer with Inno Setup..."
    & $iscc packaging\slumbr.iss
    Write-Host "==> Installer: packaging\dist-installer\slumbr-setup-cpu.exe"
} else {
    Write-Host "Inno Setup not found. Install from https://jrsoftware.org/isinfo.php"
    Write-Host "then re-run, or just zip dist\Slumbr\ as a portable build."
}
