# Contributing to Slumbr

Thanks for your interest. Slumbr is a small Windows-only dictation app maintained by Sleepy Productions. This guide covers what you need to know before opening an issue or PR.

## Ground rules

Slumbr has a handful of deliberate design constraints. PRs that violate them will be redirected before review:

- **Windows-only.** Slumbr talks directly to WASAPI, the Win32 keyboard hook, the Windows clipboard, and `SetForegroundWindow`. No cross-platform abstraction.
- **Pluggable, hardware-adaptive backends.** A first-launch wizard detects the user's GPU/CPU and picks a backend (NVIDIA CUDA · AMD/Intel DirectML · CPU). The CPU engine (Moonshine) is the universal fallback and runs on any machine — so don't hardcode one engine; everything goes behind the `Transcriber` protocol (`slumbr/stt/protocol.py`).
- **Fully local at runtime.** The two ASR models download once from Hugging Face on first launch and are cached. After that, **no network calls** — that's the privacy promise.
- **No paid / non-OSS dependencies.** PySide6 (LGPL) over PyQt6 (GPL) is intentional so the project can be redistributed freely.
- **Tap-to-toggle UX.** Not press-and-hold, not wake-word. One press starts, the next stops.

If you'd like to propose changing any of these, open an issue first — don't start with a PR.

## Dev setup

```powershell
git clone https://github.com/SIeepyDev/slumbr.git
cd slumbr
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

Run the app:

```powershell
python -m slumbr
python -m slumbr --debug   # verbose logs
```

Lint / format:

```powershell
ruff check .
ruff format .
```

Tests:

```powershell
pytest
```

CI runs the same suite on Windows across Python 3.10–3.12 on every push/PR (`.github/workflows/test.yml`); `ruff` lint + format run alongside it.

## Building the installers (maintainers)

```powershell
pip install -e ".[package]"   # PyInstaller — see the version note below
.\packaging\build.bat         # or run the PyInstaller spec directly
```

Slumbr pins **PyInstaller 6.8.0** (the `package` extra). Build with that exact version: newer PyInstaller releases have changed bootloader/hook behavior in ways that previously broke the frozen CUDA preload and the no-console wrapper. If you bump it, do a full clean-machine first-run test of **both** the CPU and NVIDIA installers before shipping.

## Architecture in a paragraph

Single Python process. One `QApplication`. A `pystray.Icon` runs the tray in its own thread. The state machine (`IDLE → RECORDING → TRANSCRIBING → PASTING → IDLE`) lives on the Qt main thread. The **final** transcribe is produced by a pluggable backend chosen for the user's hardware (faster-whisper/CUDA, ONNX DirectML, or Moonshine/CPU) via `slumbr/stt/factory.py`, run off the main thread in a `QThread`. A **streaming** engine (Moonshine + Silero VAD + online punctuation, always on CPU) drives the live popup partials while you speak. PortAudio captures audio on its own callback thread and emits a Qt signal so the visualizer can paint safely on the main thread.

If you're touching threading code, read the docstrings at the top of `slumbr/app.py`, `slumbr/audio/capture.py`, and `slumbr/input/hotkey.py` first — they document why each thread exists and what it's allowed to do.

## File map

- `slumbr/app.py` — `SlumbrApp` orchestrator. State machine, signal wiring, paste pipeline.
- `slumbr/config.py` — `SlumbrConfig` + `BackendConfig` dataclasses, atomic JSON persistence at `%APPDATA%\Slumbr\config.json`.
- `slumbr/hardware/probe.py` / `recommend.py` — detect the user's GPU/CPU and recommend a backend.
- `slumbr/stt/protocol.py` — the `Transcriber` protocol every backend implements.
- `slumbr/stt/factory.py` — builds the right backend from a `BackendConfig`.
- `slumbr/stt/backends/{whisper_ct2,directml,whispercpp,moonshine}.py` — the backends (lazy-imported).
- `slumbr/stt/streaming_engine.py` — Moonshine + Silero VAD + online punctuation for live popup partials.
- `slumbr/stt/worker.py` — QThread that runs the final transcription off the main thread.
- `slumbr/audio/capture.py` — always-on PortAudio stream + pre-buffer + ring-buffer-to-Qt bridge.
- `slumbr/audio/mirror.py` — virtual-cable routing (universal reverse-PTT: mute other apps while dictating).
- `slumbr/input/hotkey.py` / `keymap.py` — configurable 1–4 key combo hook with selective OS suppression.
- `slumbr/input/foreground.py` / `mute_key.py` / `paste.py` — foreground tracker, reverse-PTT key sender, clipboard/type paste dispatcher.
- `slumbr/ui/setup_wizard.py` — first-launch hardware-detect + backend picker. `preparing.py` — engine warm-up dialog (with CPU fallback).
- `slumbr/ui/settings_dialog.py` + `ui/tabs/` — the tabbed Settings dialog. `popup.py` — the dictation popup. `tray.py` — the tray icon/menu.
- `slumbr/theme.py` / `branding.py` — house design tokens (monochrome white + a user-pickable neutral accent) and the brand mark. **Pull colors from `theme.py`; don't hardcode hex in widget code.**
- `slumbr/bootstrap/{install,vbcable}.py` — pip backend-install worker + VB-Cable driver installer.

## PR expectations

- Run `ruff check .` and `ruff format .` before submitting.
- Update `CHANGELOG.md` under `## [Unreleased]` if your change is user-visible.
- For UI changes, manually test the golden path (Caps Lock → popup → paste into Notepad) and at least one tricky target (VS Code chat, VS Code terminal with Ctrl+Shift+V, a browser address bar).
- Don't introduce runtime network calls. Model downloads on first launch are the only allowed exception.
- Keep commits small and meaningful. Imperative subject; "why" in the body, not "what".

## Reporting bugs

Use the issue template. Include `--debug` logs and your Windows / GPU / driver / Python versions — without that we can usually only guess. If the issue involves paste behavior, note the target app and which paste method you have selected.

## License

By contributing, you agree your contributions will be licensed under the MIT License (see [LICENSE](LICENSE)).
