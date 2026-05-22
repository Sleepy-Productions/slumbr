# install.ps1 — one-shot dev install for Slumbr on Windows.
#
# Usage:
#   .\install.ps1               # default: create .venv, install runtime + dev extras, build icon, create desktop shortcut
#   .\install.ps1 -Rebuild      # wipe an existing .venv and start fresh
#   .\install.ps1 -NoShortcut   # skip the desktop shortcut
#   .\install.ps1 -NoDevExtras  # skip pytest + ruff (smaller install)
#
# Requires Python 3.10–3.12 reachable via `py` (Windows launcher) or `python`.
# Designed to be idempotent — re-running upgrades pip + dep set without rebuilding the world.

[CmdletBinding()]
param(
    [switch]$Rebuild,
    [switch]$NoShortcut,
    [switch]$NoDevExtras
)

$ErrorActionPreference = 'Stop'

$ROOT       = $PSScriptRoot
$VENV       = Join-Path $ROOT '.venv'
$VENV_PY    = Join-Path $VENV 'Scripts\python.exe'
$VENV_PYW   = Join-Path $VENV 'Scripts\pythonw.exe'
$ICON_PATH  = Join-Path $ROOT 'slumbr\assets\icon.ico'

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    $msg" -ForegroundColor Green }
function Write-Warn2($msg){ Write-Host "    $msg" -ForegroundColor Yellow }
function Fail($msg)       { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------- locate Python
Write-Step "Locating Python 3.10–3.12"

$pyExe = $null
$pyVersion = $null

# Prefer the Windows `py` launcher with an explicit minor version, falling back to whatever's on PATH.
$preferred = @('-3.11', '-3.12', '-3.10')
foreach ($flag in $preferred) {
    $resolved = (& py $flag -c "import sys; print(sys.executable)" 2>$null)
    if ($LASTEXITCODE -eq 0 -and $resolved) {
        $pyExe = $resolved.Trim()
        $pyVersion = (& py $flag -c "import sys; print('%d.%d.%d' % sys.version_info[:3])").Trim()
        break
    }
}

if (-not $pyExe) {
    $fallback = (Get-Command python -ErrorAction SilentlyContinue)
    if ($fallback) {
        $pyExe = $fallback.Source
        $pyVersion = (& $pyExe -c "import sys; print('%d.%d.%d' % sys.version_info[:3])").Trim()
        # Reject 3.13+ — onnxruntime + sherpa-onnx wheels lag and the install will fail confusingly.
        $major, $minor = $pyVersion.Split('.')[0..1]
        if ([int]$major -ne 3 -or [int]$minor -lt 10 -or [int]$minor -gt 12) {
            Fail "Found Python $pyVersion on PATH but Slumbr needs 3.10–3.12. Install one of those via https://python.org or the Microsoft Store and re-run."
        }
    }
}

if (-not $pyExe) {
    Fail "No Python 3.10–3.12 found. Install one via https://python.org (check 'Add to PATH' during setup), then re-run this script."
}

Write-Ok "Using $pyExe (Python $pyVersion)"

# ----------------------------------------------------------------- venv
if ($Rebuild -and (Test-Path $VENV)) {
    Write-Step "Rebuilding venv (you passed -Rebuild)"
    Remove-Item -Recurse -Force $VENV
}

if (-not (Test-Path $VENV_PY)) {
    Write-Step "Creating venv at .venv"
    & $pyExe -m venv $VENV
    if (-not (Test-Path $VENV_PY)) { Fail "venv creation failed — no $VENV_PY" }
    Write-Ok "venv created"
} else {
    Write-Ok "Reusing existing venv at .venv"
}

# ----------------------------------------------------------------- pip + deps
Write-Step "Upgrading pip"
& $VENV_PY -m pip install --upgrade pip --disable-pip-version-check | Out-Host
if ($LASTEXITCODE -ne 0) { Fail "pip upgrade failed" }

$extras = if ($NoDevExtras) { '' } else { '[dev]' }
Write-Step "Installing Slumbr$extras (this pulls ~2.8 GB on a cold cache — CUDA + PySide6 + faster-whisper)"
& $VENV_PY -m pip install -e ".$extras" | Out-Host
if ($LASTEXITCODE -ne 0) { Fail "pip install -e .$extras failed" }
Write-Ok "deps installed"

# ----------------------------------------------------------------- icon
Write-Step "Building icon"
& $VENV_PY (Join-Path $ROOT 'scripts\build_icon.py') | Out-Host
if ($LASTEXITCODE -ne 0) { Fail "icon build failed" }
if (-not (Test-Path $ICON_PATH)) { Fail "icon build reported success but $ICON_PATH is missing" }

# ----------------------------------------------------------------- shortcut
if ($NoShortcut) {
    Write-Warn2 "Skipping desktop shortcut (you passed -NoShortcut)"
} else {
    Write-Step "Creating desktop shortcut (Slumbr.lnk)"
    # pywin32 is optional — install_shortcut.py falls back to a VBScript writer
    # if it's missing, but installing it once means the .lnk is created in-line
    # rather than asking the user to double-click a .vbs file.
    & $VENV_PY -m pip install --quiet pywin32 | Out-Host
    & $VENV_PY (Join-Path $ROOT 'scripts\install_shortcut.py') | Out-Host
    if ($LASTEXITCODE -ne 0) { Write-Warn2 "shortcut creation hit an error — re-run scripts\install_shortcut.py manually" }
}

# ----------------------------------------------------------------- done
Write-Host ""
Write-Host "Slumbr is installed." -ForegroundColor Green
Write-Host ""
Write-Host "Launch options:"
Write-Host "  - Double-click the 'Slumbr' shortcut on your desktop (no console window)"
Write-Host "  - From this folder:  .\.venv\Scripts\pythonw.exe -m slumbr"
Write-Host "  - With logs:         .\.venv\Scripts\python.exe -m slumbr --debug"
Write-Host ""
Write-Host "First launch will download ~1.5 GB of Whisper weights from Hugging Face."
Write-Host "After that, Slumbr is fully offline. Tap Caps Lock to dictate."
