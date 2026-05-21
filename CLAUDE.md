# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

Slumbr is a **Windows-only, CUDA-accelerated, Python** dictation runner. Tap a global hotkey → small popup with live audio waveform → speak → tap again → Whisper transcribes locally → text pastes at the cursor. Fully offline at runtime.

The repo is in **early scaffold state** — most of the architecture below is planned, not yet implemented. The implementation plan is the source of truth:

- **Plan file:** `C:\Users\Sleepy\.claude\plans\i-wanted-to-know-validated-adleman.md`

Read the plan before making non-trivial changes. It documents the phased build order (Phase 0 = repo setup, Phase 1 = headless MVP, … Phase 5 = packaging) and the reasoning behind every dependency choice.

## Non-negotiable constraints

These are deliberate design decisions, not defaults. Don't change them without explicit user buy-in:

- **Windows-only.** No cross-platform abstraction. We talk to WASAPI, Win32 hotkeys, and the Windows clipboard directly.
- **CUDA required.** `faster-whisper` runs on `device="cuda"` with `compute_type="int8"`. CPU fallback is not a goal.
- **Fully local at runtime.** The Whisper model downloads once from Hugging Face on first launch and is cached. After that, **the app must not make any network call**. No telemetry, no analytics, no remote config.
- **No paid / non-OSS dependencies.** Every dep is permissively licensed. PySide6 (LGPL) was chosen over PyQt6 (GPL) specifically so the project can be redistributed freely; don't swap them.
- **Tap-to-start / tap-to-stop UX.** Not press-and-hold, not wake-word. One press starts recording, the next press stops it.

## Locked tech stack (do not substitute casually)

| Concern | Choice | Why |
|---|---|---|
| STT engine | `faster-whisper==1.2.1` + `large-v3-turbo` (int8) | Best CUDA-on-Windows DevX; ~400 ms for a 5 s utterance on RTX-class GPUs. |
| Audio capture | `sounddevice==0.3.12` (WASAPI) | Lowest-latency Python audio; clean start/stop. |
| Global hotkey | `pynput==1.7.6` | Works on Win11 *without admin*. (`keyboard` needs admin.) |
| Paste | `pyperclip` + `pynput` Ctrl+V | Unicode-safe, works in Electron/web apps. |
| UI | `PySide6==6.7.0` + `pyqtgraph` | LGPL Qt + real-time-capable plotting. |
| Tray | `pystray` + `Pillow` | Lighter than Qt's tray for tray-only chrome. |
| Packaging | `pyinstaller==6.8.0` (`--onedir`) | CUDA DLLs make `--onefile` impractical. |

## Architecture (critical: read before touching threading)

### Process model
Single Python process. One `QApplication`. A `pystray.Icon` runs the tray. Two `QDialog`s exist: the recording popup and the settings window.

### State machine
`IDLE → RECORDING → TRANSCRIBING → PASTING → IDLE`. Hotkey toggles `IDLE ↔ RECORDING`. Re-triggers during `TRANSCRIBING` / `PASTING` are no-ops. There is also a 200 ms debounce after every state transition.

### Threading (this is the part that breaks if you get it wrong)
Three threads with strict roles:

1. **Qt main thread** — UI only. Owns the dialogs, the tray callbacks, and the state machine.
2. **Audio thread** — `sounddevice` callback. *Never paints.* Pushes numpy chunks into a ring buffer and emits a `samples_ready` Qt signal with `Qt.QueuedConnection` so the popup can redraw on the main thread.
3. **Worker thread** — runs `WhisperModel.transcribe()`. Emits a `transcript_ready` signal back to the main thread.

`pynput` hotkey callbacks fire on **its own input thread** — they must *not* call UI or model code directly. Emit a Qt signal and let the main thread react.

### Model warm-up
`stt/engine.py` runs a dummy 0.5 s silence transcription at startup. Without it, the first real transcription takes ~3 s instead of ~400 ms.

### Paste flow
1. Snapshot current clipboard (if "preserve clipboard" is on).
2. `pyperclip.copy(transcript)`.
3. Send Ctrl+V via `pynput.keyboard.Controller`.
4. If auto-send is on, send Enter.
5. Sleep ~80 ms (let Ctrl+V be consumed), then restore the snapshot. Skipping the sleep loses the race and pastes the old clipboard.

### Config
JSON at `%APPDATA%\Slumbr\config.json`. Loaded into a dataclass at startup; settings UI mutates the dataclass and writes back on OK. A `config_changed` Qt signal lets the rest of the app react without restarting.

## Commands

All commands assume PowerShell from the project root.

### One-time dev setup
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

### Run the app
```powershell
python -m slumbr
```

### Tests
```powershell
pytest                                  # all
pytest tests/test_config.py             # one file
pytest tests/test_state.py::test_idle_to_recording   # one test
```

### Lint / format
```powershell
ruff check .
ruff format .
```

### Package (Phase 5 only)
```powershell
pip install -e ".[package]"
pyinstaller --noconfirm --windowed --icon=slumbr/assets/icon.ico --name Slumbr --onedir slumbr/__main__.py
```
Do **not** bundle the Whisper model into the installer — it's 1.5 GB and downloads on first run anyway.

## Repo conventions

- `.claude/` is gitignored — per-machine settings, never commit.
- Default branch is `main`. Even on a solo private repo, prefer PRs over direct pushes so history stays clean for an eventual public flip.
- Commit messages: imperative subject, why-not-what body. The plan file is the design doc; commits explain change motivation.

## When in doubt

- Architecture / "why this dep" questions → plan file at `C:\Users\Sleepy\.claude\plans\i-wanted-to-know-validated-adleman.md`.
- Library API questions → fetch current docs via Context7 (`faster-whisper`, `PySide6`, `pynput`, etc.) rather than relying on training data; the ecosystem moves fast.
- Reference implementations to learn from: [Buzz](https://github.com/chidiwilliams/buzz) (production patterns), [whisper-writer](https://github.com/savbell/whisper-writer) (minimal hotkey-paste loop).
