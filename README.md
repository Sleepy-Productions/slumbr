# Slumbr

**Local, offline, hotkey-driven voice-to-text dictation for Windows.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Platform: Windows](https://img.shields.io/badge/platform-windows-lightgrey.svg)](#requirements)
[![Status: 0.2.0 Alpha](https://img.shields.io/badge/status-0.2.0--alpha-orange.svg)](#)

Tap **Caps Lock** → a small popup appears with a live audio meter and partial transcript → speak → tap **Caps Lock** again → your words appear at the cursor. Fully on-device. No accounts, no cloud, no telemetry.

<!-- TODO: insert demo GIF at docs/demo.gif -->

## Features

- **Pluggable STT backends, auto-picked per hardware.** First launch probes your GPU and pip-installs only the right runtime:
  - NVIDIA RTX → `faster-whisper` on CUDA (max accuracy + speed)
  - AMD Radeon RX → Whisper via ONNX Runtime DirectML
  - Intel Arc + iGPU → DirectML (SYCL on roadmap)
  - CPU-only → Moonshine Small (~150–300 ms, snappier than Whisper on CPU)
- **Live partials while you speak.** Moonshine + Silero VAD + online punctuation give the popup smooth word-by-word text that grows monotonically.
- **Tap-to-toggle Caps Lock hotkey.** No press-and-hold, no wake-word. The OS-level Caps Lock state is never flipped while Slumbr is running.
- **Auto-paste at the cursor.** Works in Notepad, browsers, chat apps, terminals (Ctrl+Shift+V mode), and Electron IDEs like VS Code.
- **Reverse PTT — two ways:**
  1. **Virtual mic routing** (universal): one-click VB-Cable install + auto-config → call apps hear silence during dictation, Slumbr keeps capturing. Works in Discord / Zoom / Teams / OBS / browser calls.
  2. **Send-keybind hack** (Discord-specific): Slumbr presses a configured keybind during dictation; user binds it to Discord's "Push To Mute" setting.
- **System tray + tabbed Settings dialog**, all themed in the Sleepy Productions violet. No hub window — the tray is the only persistent UI surface.
- **Transcript history.** Last 50 dictations at `%APPDATA%\Slumbr\history.jsonl`, surfaced in Settings → History and in the tray menu's `Last:` header.
- **Customizable.** Input device, language, vocabulary hint, paste method, auto-send, hotkey, backend, model size, compute precision, reverse-PTT mode.

## How it works

Slumbr runs **two ASR engines** in parallel because Whisper isn't streaming-native:

| Engine | Job | Model | Latency |
| --- | --- | --- | --- |
| Moonshine + LocalAgreement-2 (CPU, ONNX int8) | Live popup partials while you speak | ~180 MB streaming + punctuation | ~150–300 ms to first word |
| Selected primary backend (CUDA / DirectML / CPU) | Final transcript at Caps Lock release | Whisper `large-v3` / `small` / Moonshine | ~400 ms – 1 s for a 5 s utterance, hardware-dependent |

All models cache to `%APPDATA%\Slumbr\models` on first download. After that, Slumbr makes **zero network calls** at runtime.

## Requirements

- **Windows 10/11**
- **Python 3.10–3.12**
- ~3–4 GB free disk (varies by backend; CPU-only is the smallest at ~1 GB)
- **GPU optional.** Any of:
  - NVIDIA RTX with CUDA 12.x + driver 560+ (best perf — ships cuBLAS/cuDNN wheels)
  - AMD Radeon RX with DX12 driver (uses DirectML — no ROCm needed)
  - Intel Arc or recent Iris/UHD iGPU (DirectML)
  - No GPU — Moonshine Small runs cleanly on a modern desktop CPU

## Install (dev)

```powershell
git clone https://github.com/SIeepyDev/slumbr.git
cd slumbr
.\install.ps1
```

`install.ps1` locates Python 3.10–3.12, creates `.venv`, installs the base runtime, builds the icon, and drops a Slumbr shortcut on your desktop. On first launch, the **setup wizard** probes your hardware and pip-installs the right vendor wheels (~50 MB–1.9 GB depending on backend).

Useful flags:

```powershell
.\install.ps1 -Backend nvidia    # pre-bake NVIDIA wheels (skip wizard's install step)
.\install.ps1 -Backend amd       # pre-bake DirectML wheels
.\install.ps1 -Backend cpu       # pre-bake CPU-only path
.\install.ps1 -Rebuild           # wipe .venv and start clean
.\install.ps1 -NoShortcut        # skip the desktop shortcut
.\install.ps1 -NoDevExtras       # skip pytest + ruff
```

Launch options after install:

```powershell
.\.venv\Scripts\pythonw.exe -m slumbr       # via the desktop shortcut path (no console)
.\.venv\Scripts\python.exe -m slumbr --debug # with verbose logs
```

Default hotkey: **Caps Lock** (tap to start, tap to stop). Rebind from Settings → Shortcuts.

## Reverse PTT setup

The universal path (recommended):

1. Right-click tray → Settings → **Behavior** tab
2. Under "Virtual mic routing", click **"Install VB-Cable"** (Windows will prompt for admin)
3. Reboot Windows (kernel driver requirement)
4. Re-launch Slumbr → the status flips to "Detected 1 virtual cable"
5. Tick **"Route my mic through a virtual cable"**
6. **In your call apps:** set the microphone to **"CABLE Output (VB-Audio Virtual Cable)"** (note: "Output" — VB-Cable names from the cable's perspective)
7. Keep your *speaker* on your real headphones

Now Caps Lock silences your mic in every call app while Slumbr keeps transcribing internally.

## Privacy

Slumbr never makes a network call at runtime. Models download once from Hugging Face on first launch, cached at `%APPDATA%\Slumbr\models`. Audio buffers live in RAM only and are discarded after transcription. Transcripts persist locally at `%APPDATA%\Slumbr\history.jsonl` (last 50 entries, plain JSON; clear from Settings → History). No accounts, no telemetry, no analytics.

## Troubleshooting

**Paste doesn't work in VS Code's integrated terminal (or Windows Terminal).**
Terminals reserve Ctrl+V. Settings → Behavior → Paste method → **Ctrl+Shift+V**.

**First utterance is slow.**
Warm-up runs at startup; the first *real* transcription still pays a small one-time decoder cost. Subsequent utterances settle into the steady-state range.

**"cublas64_12.dll not found" or similar CUDA error.**
You're on the NVIDIA backend but the wheels didn't land. Either re-run the wizard (Settings → Engine → switch + back to NVIDIA) or `pip install -e .[nvidia]` against your venv manually.

**Mic doesn't show up / wrong device picked.**
Settings → Voice → Input device. Slumbr stores names (not numeric indices) so USB-mic hot-plug survives. On Windows with VB-Cable installed, **don't pick CABLE Output as your mic** — that's the cable's loopback side, not your real mic.

**Discord (or another call app) doesn't hear me.**
Verify: Settings → Behavior → "Route my mic through a virtual cable" is on AND the device dropdown shows the right cable. In Discord, the mic must be **"CABLE Output (VB-Audio Virtual Cable)"** — counterintuitive name, but correct. Slumbr's log at `%APPDATA%\Slumbr\logs\slumbr.log` will show `MicMirror started …` if routing is live.

**Model download is large/slow.**
The Whisper large models are 1.5–3 GB. Switch to `small` or `Moonshine` in Settings → Engine if you need a smaller footprint.

## Limitations

- **Windows-only.** WASAPI, Win32 hotkey hook, and Windows clipboard APIs aren't abstracted.
- **Reverse PTT needs VB-Cable** for the universal path. The Discord-PTM hack works without it but only in Discord.
- **First-run model downloads** total 200 MB – 3 GB depending on backend; not feasible offline.
- **Moonshine is English-only.** Settings → Engine routes non-English users to Whisper backends automatically.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup, architecture notes, and PR guidelines.

## License

MIT — see [LICENSE](LICENSE).

---

Built by [Sleepy Productions](https://github.com/SIeepyDev).
