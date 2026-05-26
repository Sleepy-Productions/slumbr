# install.ps1 — one-shot dev install for Slumbr on Windows.
#
# Usage:
#   .\install.ps1                  # base install only — Slumbr's first-launch wizard
#                                  # detects your hardware and pip-installs the right backend
#                                  # (NVIDIA / AMD / Intel / CPU) for you.
#   .\install.ps1 -Backend nvidia  # pre-install a specific backend's extras up-front
#                                  # (skips the wizard's install step).
#                                  # Valid: nvidia | amd | intel | cpu
#   .\install.ps1 -Rebuild         # wipe an existing .venv and start fresh
#   .\install.ps1 -NoShortcut      # skip the desktop shortcut
#   .\install.ps1 -NoDevExtras     # skip pytest + ruff (smaller install)
#
# Requires Python 3.10–3.12 reachable via `py` (Windows launcher) or `python`.
# Designed to be idempotent — re-running upgrades pip + dep set without rebuilding the world.
#
# Phase 2 change: NVIDIA / AMD / Intel / CPU backend wheels moved into
# `[project.optional-dependencies]` extras. A bare `install.ps1` no longer
# downloads 1.9 GB of CUDA wheels onto AMD machines — the wizard makes
# that choice at first launch based on actual hardware.

[CmdletBinding()]
param(
    [ValidateSet('nvidia', 'amd', 'intel', 'cpu', '')]
    [string]$Backend = '',
    [switch]$Rebuild,
    [switch]$NoShortcut,
    [switch]$NoDevExtras
)

$ErrorActionPreference = 'Stop'

$ROOT       = $PSScriptRoot
$VENV       = Join-Path $ROOT '.venv'
$VENV_PY    = Join-Path $VENV 'Scripts\python.exe'
$VENV_PYW   = Join-Path $VENV 'Scripts\pythonw.exe'
$VENV_SLUMBR= Join-Path $VENV 'Scripts\Slumbr.exe'
$ICON_PATH  = Join-Path $ROOT 'slumbr\assets\icon.ico'

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    $msg" -ForegroundColor Green }
function Write-Warn2($msg){ Write-Host "    $msg" -ForegroundColor Yellow }
function Fail($msg)       { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

function Reset-IconCache {
    # Slumbr's icon is monochrome WHITE (slumbr/branding.py LOGO_COLOR = #FFFFFF),
    # baked fresh into icon.ico just above. But Windows caches shell icons by path
    # and can keep serving a STALE bitmap from an earlier build (this is why the
    # desktop icon used to "randomly" come back tinted) — overwriting the .ico does
    # NOT reliably invalidate that cache. So we force the shell to forget it.
    # Every step is best-effort: a locked cache file or missing tool must never
    # fail the install.
    Write-Step "Refreshing Windows icon cache (so the white icon shows immediately)"
    try {
        # 1) Drop the per-user icon-cache databases — Explorer rebuilds them.
        $patterns = @(
            (Join-Path $env:LOCALAPPDATA 'IconCache.db'),
            (Join-Path $env:LOCALAPPDATA 'Microsoft\Windows\Explorer\iconcache_*.db')
        )
        foreach ($p in $patterns) {
            Get-ChildItem -Path $p -Force -ErrorAction SilentlyContinue | ForEach-Object {
                try { Remove-Item $_.FullName -Force -ErrorAction Stop } catch {}
            }
        }
        # 2) Ask the shell to rebuild its icon cache.
        try { & ie4uinit.exe -show 2>$null } catch {}
        # 3) Broadcast SHCNE_ASSOCCHANGED so already-open Explorer windows repaint
        #    the icon now — WITHOUT a disruptive full Explorer restart (important
        #    when someone else runs this installer).
        if (-not ([System.Management.Automation.PSTypeName]'Slumbr.Shell').Type) {
            Add-Type -Namespace 'Slumbr' -Name 'Shell' -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("shell32.dll")]
public static extern void SHChangeNotify(int eventId, uint flags, System.IntPtr i1, System.IntPtr i2);
'@
        }
        [Slumbr.Shell]::SHChangeNotify(0x08000000, 0x0000, [System.IntPtr]::Zero, [System.IntPtr]::Zero)
        Write-Ok "icon cache refreshed (white icon active)"
    } catch {
        Write-Warn2 "icon-cache refresh skipped (non-fatal): $($_.Exception.Message)"
    }
}

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

# Build the extras token. We always want `dev` unless -NoDevExtras; we add
# the chosen backend if -Backend was given. Anything not in the extras list
# is deferred to the wizard's Install screen at first launch.
$extrasList = @()
if (-not $NoDevExtras) { $extrasList += 'dev' }
if ($Backend) { $extrasList += $Backend }
$extras = if ($extrasList.Count -gt 0) { "[" + ($extrasList -join ',') + "]" } else { '' }

if ($Backend) {
    Write-Step "Installing Slumbr$extras (pre-baking the $Backend backend's wheels)"
} else {
    Write-Step "Installing Slumbr$extras (base only — Slumbr's wizard installs the right backend on first launch)"
}
& $VENV_PY -m pip install -e ".$extras" | Out-Host
if ($LASTEXITCODE -ne 0) { Fail "pip install -e .$extras failed" }
Write-Ok "deps installed"

# ----------------------------------------------------------------- icon
Write-Step "Building icon"
& $VENV_PY (Join-Path $ROOT 'scripts\build_icon.py') | Out-Host
if ($LASTEXITCODE -ne 0) { Fail "icon build failed" }
if (-not (Test-Path $ICON_PATH)) { Fail "icon build reported success but $ICON_PATH is missing" }

# ----------------------------------------------------------------- launcher
# Copy pythonw.exe -> Slumbr.exe so the RUNNING process is "Slumbr.exe", not the
# host "pythonw.exe". That process name is what the taskbar button shows and what
# pinning latches onto — the AUMID only fixes grouping, this fixes the identity
# so it never reads as "Python". (Python doesn't care about its exe filename.)
Write-Step "Creating Slumbr.exe launcher (so it runs + pins as Slumbr, not Python)"
Copy-Item $VENV_PYW $VENV_SLUMBR -Force
if (Test-Path $VENV_SLUMBR) { Write-Ok "Slumbr.exe ready" }
else { Write-Warn2 "couldn't create Slumbr.exe — the shortcut will fall back to pythonw.exe" }

# ----------------------------------------------------------------- shortcut
if ($NoShortcut) {
    Write-Warn2 "Skipping desktop shortcut (you passed -NoShortcut)"
} else {
    Write-Step "Creating desktop shortcut (Slumbr.lnk)"
    & $VENV_PY -m pip install --quiet pywin32 | Out-Host
    & $VENV_PY (Join-Path $ROOT 'scripts\install_shortcut.py') | Out-Host
    if ($LASTEXITCODE -ne 0) { Write-Warn2 "shortcut creation hit an error — re-run scripts\install_shortcut.py manually" }
    Reset-IconCache
}

# ----------------------------------------------------------------- done
Write-Host ""
Write-Host "Slumbr is installed." -ForegroundColor Green
Write-Host ""
Write-Host "Launch options:"
Write-Host "  - Double-click the 'Slumbr' shortcut on your desktop (no console window)"
Write-Host "  - From this folder:  .\.venv\Scripts\Slumbr.exe -m slumbr"
Write-Host "  - With logs:         .\.venv\Scripts\python.exe -m slumbr --debug"
Write-Host ""
if ($Backend) {
    Write-Host "Backend pre-baked: $Backend"
    Write-Host "  Slumbr will skip the install step of the first-launch wizard."
} else {
    Write-Host "First launch:"
    Write-Host "  Slumbr's wizard will detect your hardware and pip-install the right backend"
    Write-Host "  (NVIDIA: ~1.9 GB; AMD/Intel: ~600 MB; CPU: ~50 MB). Have an internet"
    Write-Host "  connection ready for the first run."
}
Write-Host ""
Write-Host "After install, the first transcribe downloads ~1.5 GB of Whisper weights from"
Write-Host "Hugging Face. After that, Slumbr is fully offline. Tap Caps Lock to dictate."
