# Build the NVIDIA Slumbr installer (faster-whisper on CUDA).
#
#   pwsh packaging\build_nvidia.ps1
#
# Builds from a venv with the [cuda] extra. By default reuses the dev .venv
# (which already has the CUDA stack); pass -FreshVenv to build a dedicated
# .venv-nvidia instead. Much bigger than the CPU build (~1.5-2 GB) since it
# bundles the CUDA runtime DLLs.
#
# NOT yet verified end-to-end — expect an iteration pass on CUDA DLL loading
# in the frozen bundle (see the spec header). Run dist\Slumbr\Slumbr.exe and
# confirm a transcribe actually uses the GPU before shipping.
param(
    [switch]$FreshVenv,
    [switch]$SkipInstaller
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if ($FreshVenv) {
    Write-Host "==> Creating a dedicated CUDA build venv (.venv-nvidia)..."
    if (Test-Path .venv-nvidia) { Remove-Item -Recurse -Force .venv-nvidia }
    python -m venv .venv-nvidia
    $py = ".\.venv-nvidia\Scripts\python.exe"
    & $py -m pip install --upgrade pip
    # -c build-constraints.txt pins PySide6/sounddevice/pynput/pyinstaller to the
    # EXACT tested versions — newer majors segfault the frozen bundle (see
    # build_cpu.ps1). DO NOT remove.
    & $py -m pip install -c packaging\build-constraints.txt ".[cuda]"
    & $py -m pip install -c packaging\build-constraints.txt pyinstaller
} else {
    $py = ".\.venv\Scripts\python.exe"   # dev venv already has [cuda]
    & $py -m pip install -c packaging\build-constraints.txt pyinstaller
}

Write-Host "==> Cleaning previous build..."
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue

Write-Host "==> Running PyInstaller (NVIDIA — bundles CUDA, this is large)..."
& $py -m PyInstaller packaging\slumbr-nvidia.spec --noconfirm
Write-Host "==> Portable onedir built: dist\Slumbr\  (test: dist\Slumbr\Slumbr.exe)"

if ($SkipInstaller) { return }
$iscc = @(
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($iscc) {
    & $iscc /DFlavor=nvidia packaging\slumbr.iss
    Write-Host "==> Installer: packaging\dist-installer\slumbr-setup-nvidia.exe"
} else {
    Write-Host "Inno Setup not found (winget install JRSoftware.InnoSetup)."
}
