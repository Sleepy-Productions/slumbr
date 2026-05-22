# Contributing to Slumbr

Thanks for your interest. Slumbr is a small Windows-only dictation app maintained by Sleepy Productions. This guide covers what you need to know before opening an issue or PR.

## Ground rules

Slumbr has a handful of deliberate design constraints. PRs that violate them will be redirected before review:

- **Windows-only.** Slumbr talks directly to WASAPI, the Win32 keyboard hook, the Windows clipboard, and `SetForegroundWindow`. No cross-platform abstraction.
- **CUDA required at runtime.** `faster-whisper` is configured for `device="cuda"` with `compute_type="int8"`. CPU fallback is not a goal.
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

Tests (when they exist — currently pre-alpha):

```powershell
pytest
```

## Architecture in a paragraph

Single Python process. One `QApplication`. A `pystray.Icon` runs the tray in its own thread. The state machine (`IDLE → RECORDING → TRANSCRIBING → PASTING → IDLE`) lives on the Qt main thread. **Two ASR engines** run in parallel: sherpa-onnx Zipformer (CPU, streaming) drives the popup partials while you speak; faster-whisper (CUDA, non-streaming) produces the final paste at Caps Lock release. PortAudio captures audio on its own callback thread and emits a Qt signal so the visualizer can paint safely on the main thread.

If you're touching threading code, read the docstrings at the top of `slumbr/app.py`, `slumbr/audio/capture.py`, and `slumbr/input/hotkey.py` first — they document why each thread exists and what it's allowed to do.

## File map

- `slumbr/app.py` — `SlumbrApp` orchestrator. State machine, signal wiring, paste pipeline.
- `slumbr/audio/capture.py` — always-on PortAudio stream + 500 ms pre-buffer + ring-buffer-to-Qt bridge.
- `slumbr/stt/engine.py` — faster-whisper wrapper with warm-up + OOM-aware retry.
- `slumbr/stt/streaming_engine.py` — sherpa-onnx Zipformer wrapper for live popup partials.
- `slumbr/stt/worker.py` — QThread that runs Whisper transcription off the main thread.
- `slumbr/input/hotkey.py` — Caps Lock low-level hook with full OS suppression.
- `slumbr/input/foreground.py` — 10 Hz foreground-window tracker.
- `slumbr/input/paste.py` — clipboard + Ctrl[+Shift]+V dispatcher, with tuned timing constants.
- `slumbr/ui/main_window.py` / `popup.py` / `settings.py` / `tray.py` / `about.py` — Qt widgets.
- `slumbr/theme.py` — single source of truth for the brand violet palette. **Never hardcode hex strings in widget code.**
- `slumbr/config.py` — `SlumbrConfig` dataclass + atomic JSON persistence at `%APPDATA%\Slumbr\config.json`.

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
