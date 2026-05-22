# Slumbr

**Local, offline, hotkey-driven voice-to-text dictation for Windows.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Platform: Windows](https://img.shields.io/badge/platform-windows-lightgrey.svg)](#requirements)
[![Status: Pre-alpha](https://img.shields.io/badge/status-pre--alpha-orange.svg)](#)

Tap **Caps Lock** → a small popup appears with a live audio waveform and live partial transcript → speak → tap **Caps Lock** again → your words appear at the cursor. Fully on-device. No accounts, no cloud, no telemetry.

<!-- TODO: insert demo GIF at docs/demo.gif -->

## Features

- **Fully local STT.** [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (Whisper `large-v3` / `large-v3-turbo`, int8 on CUDA) for the final paste.
- **Live partials** while you speak — true streaming transducer (sherpa-onnx Zipformer) so the popup text only grows, no flicker.
- **Tap-to-toggle Caps Lock hotkey.** No press-and-hold, no wake-word. The OS-level Caps Lock state is never flipped while Slumbr is running.
- **Auto-paste at the cursor.** Works in Notepad, browsers, chat apps, terminals (Ctrl+Shift+V mode), and Electron-based IDEs like VS Code.
- **Optional auto-send** — presses Enter after pasting for chat workflows.
- **System tray + main window + settings dialog**, all themed in the Sleepy Productions violet.
- **Customizable:** input device, mic gain, Whisper model size, language, vocabulary hint, paste method, close-to-tray behavior.

## How it works

Slumbr runs **two ASR engines** in parallel because Whisper isn't streaming-native:

| Engine | Job | Model | Latency |
| --- | --- | --- | --- |
| sherpa-onnx Zipformer (CPU, int8) | Live popup partials while you speak | ~71 MB streaming transducer | ~300 ms to first word |
| faster-whisper (CUDA, int8) | The final paste at Caps Lock release | `large-v3` / `large-v3-turbo` | ~250–400 ms for a 5 s utterance |

Both models download from Hugging Face on first launch and are cached locally. After that, Slumbr makes **zero network calls** at runtime.

## Requirements

- **Windows 10/11**
- **NVIDIA GPU** with **CUDA 12.x** + **cuDNN 9.x** + driver **560+**
- **Python 3.10+**
- ~2 GB free disk for the Whisper model (large-v3 ≈ 1.5 GB) + 71 MB for the streaming Zipformer

## Install (dev)

```powershell
git clone https://github.com/SIeepyDev/slumbr.git
cd slumbr
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
python -m slumbr
```

Default hotkey: **Caps Lock** (tap once to start, tap again to stop). Hotkey is configurable from the Settings dialog.

For verbose logs:

```powershell
python -m slumbr --debug
```

## Privacy

Slumbr never makes a network call at runtime. Both models are downloaded once from Hugging Face on first launch, cached at `%APPDATA%\Slumbr\models`, and then the app works fully offline forever. Audio buffers live in RAM only and are discarded after transcription. No accounts, no telemetry, no analytics.

## Troubleshooting

**Paste doesn't work in VS Code's integrated terminal (or Windows Terminal).**
Terminals reserve Ctrl+V as a literal character prefix. Open Settings → Paste method → choose **Ctrl+Shift+V** (the terminal default on Windows). Manual Ctrl+V also fails into terminals — this is not a Slumbr bug.

**First utterance is slow (~3 seconds).**
The warm-up pass runs at startup, but the first *real* transcription still pays a small one-time decoder cost. Subsequent utterances settle into the ~250–400 ms range.

**"cublas64_12.dll not found" or similar CUDA error.**
Slumbr expects CUDA 12.x and cuDNN 9.x via the official NVIDIA pip wheels (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`, `nvidia-cuda-nvrtc-cu12`). They're pulled in transitively by `faster-whisper`. If you see this error, verify your Python environment is the same one Slumbr was installed into.

**Microphone shows the wrong device.**
Open Settings → Input device → pick your mic by name. Slumbr stores the device name (not its numeric index) so the choice survives USB-mic hot-plug.

**Model download is large/slow.**
The Whisper model (~1.5 GB) downloads once on first launch. If you want a smaller/faster footprint at the cost of accuracy, switch to `distil-large-v3` or `medium` in Settings → Model (requires restart).

## Limitations

- **Windows-only by design.** WASAPI, Win32 hotkey hook, and Windows clipboard APIs are not abstracted.
- **CUDA required.** CPU-only Whisper is not a goal — `faster-whisper` is configured for `device="cuda"`.
- **Streaming Zipformer is English-only and emits ALL CAPS / no punctuation.** Slumbr formats popup partials for readability; the final paste comes from Whisper, which handles ~100 languages and proper casing.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup, architecture notes, and PR guidelines.

## License

MIT — see [LICENSE](LICENSE).

---

Built by [Sleepy Productions](https://github.com/SIeepyDev).
