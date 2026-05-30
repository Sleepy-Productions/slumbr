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
    # -c build-constraints.txt PINS PySide6/sounddevice/pynput/pyinstaller to the
    # EXACT tested versions for the FROZEN bundle. pyproject's loosened >= floors
    # are correct for source/pip, but newer majors (PySide6 6.11, sounddevice
    # 0.5.x, pynput 1.8) SEGFAULT the frozen exe on launch. DO NOT remove this.
    $constraints = "packaging\build-constraints.txt"
    # Base deps (sherpa-onnx Moonshine + huggingface_hub + Qt). Then add
    # faster-whisper for the cpu_ct2 backend WITHOUT the nvidia-* CUDA wheels
    # (those live in the [cuda] extra) so the bundle stays CPU-sized, plus CPU
    # onnxruntime so we never pull the DirectML build.
    & $py -m pip install -c $constraints .
    & $py -m pip install -c $constraints "faster-whisper==1.2.1"
    & $py -m pip install -c $constraints onnxruntime    # CPU provider, not -directml
    & $py -m pip install -c $constraints pyinstaller
} else {
    $py = ".\.venv\Scripts\python.exe"
    & $py -m pip install -c packaging\build-constraints.txt pyinstaller
}

Write-Host "==> Cleaning previous build..."
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue

Write-Host "==> Running PyInstaller..."
& $py -m PyInstaller packaging\slumbr-cpu.spec --noconfirm
Write-Host "==> Portable onedir built: dist\Slumbr\  (run dist\Slumbr\Slumbr.exe to test)"

if ($SkipInstaller) { return }
$iscc = @(
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($iscc) {
    Write-Host "==> Compiling installer with Inno Setup..."
    & $iscc /DFlavor=cpu packaging\slumbr.iss
    Write-Host "==> Installer: packaging\dist-installer\slumbr-setup-cpu.exe"
} else {
    Write-Host "Inno Setup not found. Install it (winget install JRSoftware.InnoSetup)"
    Write-Host "then re-run, or just zip dist\Slumbr\ as a portable build."
}
