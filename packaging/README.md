# Packaging Slumbr for distribution

Goal: a one-click Windows installer so non-developers can run Slumbr without
a Python venv.

## Why not a single .exe?
PyInstaller `--onefile` is not viable here — the full dependency set (CUDA +
PySide6 + ONNX +, for AMD, torch/transformers) balloons to 4–5 GB and the
hooks are fragile. So we ship **per-stack** builds, smallest first.

## Status
- **CPU build** (this folder) — Moonshine + faster-whisper on CPU. Runs on
  ANY Windows PC, no GPU, no Python. Widest reach; the first milestone.
- **NVIDIA / AMD flavors** — follow-up. Same approach with the GPU deps
  included (larger), shipped as separate `slumbr-setup-nvidia.exe` etc.

## Build the CPU installer
```powershell
# From the repo root:
pwsh packaging\build_cpu.ps1
```
This:
1. Creates a clean `.venv-cpu` (so CUDA/DirectML/torch don't get bundled),
   installs the package + CPU `onnxruntime` + PyInstaller.
2. Runs `slumbr-cpu.spec` → `dist\Slumbr\` (a portable onedir — test it with
   `dist\Slumbr\Slumbr.exe`).
3. If [Inno Setup 6](https://jrsoftware.org/isinfo.php) is installed,
   compiles `packaging\dist-installer\slumbr-setup-cpu.exe`.

`-UseCurrentVenv` builds from the active venv instead (faster, but larger if
it has GPU libs). `-SkipInstaller` stops after the portable onedir.

## Expected gotchas (PyInstaller + native deps)
First builds usually need a couple of iterations:
- **sherpa-onnx / ctranslate2** ship `.dll`/`.onnx` data — handled via
  `collect_all` in the spec; if a runtime error says a file is missing, add
  it to `datas`.
- **Lazy backend imports** — the factory imports backends inside functions,
  so they're listed in the spec's `hiddenimports`. Add any new backend there.
- **Model weights are NOT bundled** — Moonshine (~180 MB) downloads to
  `%APPDATA%\Slumbr` on first run, behind the new "Preparing Slumbr" dialog.
  The installer stays small; first launch needs internet once.
- Test the built exe on a **clean machine / fresh user** (no Python, no
  `%APPDATA%\Slumbr`) to catch missing-dependency and first-run-download bugs.

## Files
- `slumbr-cpu.spec` — PyInstaller spec (CPU-only excludes).
- `build_cpu.ps1` — clean-venv build + optional Inno compile.
- `slumbr.iss` — Inno Setup installer script.
