# Slumbr

Local, offline, hotkey-driven voice-to-text dictation runner for Windows.

Press a global hotkey → a small popup appears with a live audio waveform → speak → press the hotkey again → your words appear at the cursor. Everything runs on-device. No accounts, no cloud, no telemetry.

> **Status:** Pre-alpha. Active development.

## Features

- **Fully local STT** via [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (Whisper large-v3-turbo, int8 on CUDA).
- **Tap hotkey to start, tap again to stop.** No press-and-hold required.
- **Live waveform** popup while recording.
- **Auto-paste** at the current cursor — works in Notepad, browsers, chat apps, terminals, IDEs.
- **Optional auto-send** (presses Enter after pasting) for chat workflows.
- **Customizable**: hotkey, audio device, mic gain, Whisper model size, language, theme, popup position, and more.

## Requirements

- Windows 10/11
- NVIDIA GPU with **CUDA 12.x** + **cuDNN 9.x** + driver **560+**
- Python 3.10+
- ~2 GB free disk for the Whisper model (downloaded on first run)

## Install (dev)

```powershell
git clone <repo-url>
cd Slumbr
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
python -m slumbr
```

Default hotkey: `Ctrl+Alt+Space`.

## Privacy

Slumbr never makes a network call at runtime. The Whisper model is downloaded once from Hugging Face on first launch, cached locally, and then the app works fully offline forever. Audio buffers live in RAM only and are discarded after transcription.

## License

MIT — see [LICENSE](LICENSE).
