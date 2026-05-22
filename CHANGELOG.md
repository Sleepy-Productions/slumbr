# Changelog

All notable changes to Slumbr are documented in this file. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and Slumbr's versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Slumbr is in pre-1.0; the public API and config format may shift between minor releases. The first 1.0 will lock both.

## [0.2.0] — 2026-05-22

The "make it actually usable on any Windows machine" release. Lifts Slumbr off its NVIDIA-only foundation, deletes the hub window in favor of a tabbed Settings dialog, ships a first-launch wizard that picks the right backend per detected hardware, and adds universal reverse-PTT for any call app.

### Added
- **Pluggable Transcriber protocol** with concrete backends:
  - `cuda_ct2` — `faster-whisper` on CUDA (NVIDIA, max perf path)
  - `directml` — Whisper via ONNX Runtime DirectML (AMD Radeon, Intel Arc + iGPU)
  - `whispercpp_cpu` — `pywhispercpp` (universal CPU fallback for Whisper-quality at the cost of latency)
  - `moonshine` — sherpa-onnx Moonshine offline (snappy CPU default, ~150–300 ms / 5 s utterance)
- **First-launch setup wizard** (PySide6 QDialog) — hardware probe → recommendation → pip-install vendor extras → done. Wizard auto-skips the install step when the chosen backend's wheels are already present.
- **nvidia-smi VRAM override** corrects WMI's uint32-capped `AdapterRAM` field so big GPUs (RTX 4090, 5090, etc.) get the right model recommendation instead of being mis-detected as 4 GB cards.
- **Tabbed Settings dialog** replacing the deleted main window. Tabs: Engine / Voice / Behavior / Shortcuts / History / About.
- **Transcript history** at `%APPDATA%\Slumbr\history.jsonl`, ring-buffered at 50 entries. Latest entry surfaces in the tray menu's `Last:` header (auto-refreshing) and the History tab.
- **Reverse PTT** — two complementary mechanisms users can enable independently:
  - **Virtual mic routing** via VB-Audio Virtual Cable. Universal — works in Discord, Zoom, Teams, OBS, browser calls. Slumbr passes the real-mic audio through to the cable continuously, and switches the feed to silence during dictation.
  - **Discord PTM keybind** simulator. Slumbr presses a configured key during dictation; user binds it to Discord's Push-To-Mute setting.
- **VB-Cable auto-installer** in Settings → Behavior. Downloads the official VBCABLE_Driver_Pack from vb-audio.com to `%TEMP%`, launches the setup elevated via PowerShell `Start-Process -Verb RunAs`, waits, prompts reboot. No bundled binary (license-clean — fetched fresh from official source each time).
- **"Restart Slumbr"** entry in the tray menu — spawns a detached fresh process, then exits the current one. Used by the post-install handoff and for picking up settings that require a fresh boot.
- **Rotating file logger** at `%APPDATA%\Slumbr\logs\slumbr.log` (5 MB × 5 backups). Captures everything at DEBUG regardless of console verbosity, so production launches via pythonw (which discards stdout) still produce usable logs.
- **Hardware detection** at `slumbr/hardware/probe.py` — WMI primary with 3 s timeout, registry fallback, CPU-only last resort. Ranks discrete > iGPU > none for hybrid laptops.
- **Direction-aware host-API priority** in `slumbr/audio/mirror.py`. Output prefers WASAPI (low latency, adaptable to native format); input prefers DirectSound + MME (permissive resamplers that accept 16 kHz mono on devices like the HyperX QuadCast whose mix format is locked at 192 kHz).
- **`install.ps1 -Backend <vendor>`** flag for pre-baked installs (CI / power users).

### Changed
- **Main window deleted.** The persistent UI surface is now tray + recording popup + Settings dialog only. Settings opens modeless from the tray.
- **`pyproject.toml` restructured.** NVIDIA wheels (`faster-whisper`, `nvidia-cublas-cu12`, `nvidia-cudnn-cu12`, `nvidia-cuda-nvrtc-cu12`) moved out of mandatory `dependencies` into `[project.optional-dependencies].nvidia`. New `amd`, `intel`, `cpu` extras. Base install dropped from ~2.8 GB to ~500 MB.
- **`SlumbrConfig`** grows a `backend: BackendConfig | None` field. Legacy `model_size` + `compute_type` keys auto-migrate to `BackendConfig(name="cuda_ct2", …)` on load, so v0.1.0 configs skip the wizard.
- **MicMirror adapts to device-native format.** Detects each output device's `default_samplerate` / `max_output_channels` at open time and upsamples + duplicates channels in `push()` — handles VB-Cable's 48 kHz stereo lock as well as standard devices.
- **AudioRecorder** gains a `on_chunk_continuous` callback that fires on every PortAudio chunk regardless of dictation state. Required so MicMirror gets continuous audio for the call-app feed (the existing `on_chunk` stays recording-gated).
- **Tray menu** drops "Show Slumbr" (no hub to show), gains "Restart Slumbr".

### Fixed
- `TranscriptionError` moved from `slumbr/stt/engine.py` to `slumbr/stt/protocol.py` so `slumbr/stt/worker.py` no longer transitively imports `faster_whisper` — AMD/Intel/CPU users can now import worker without ctranslate2 in their venv.
- Device-name ambiguity (`sd.InputStream` / `sd.OutputStream` rejecting names that exist under multiple host APIs) handled via `resolve_device_index`, which picks an unambiguous int per direction.
- MME-truncated names (31-char clip) tolerated by the resolver's fallback path.
- Settings → Engine's auto-preselected cable now writes to config (previously the dropdown change happened before the signal was connected, so ticking the route checkbox left config with `mic_routing_device_name=""`).

## [Unreleased pre-0.2.0]

Subsumed into 0.2.0. The previous Unreleased section's items (About dialog, version footer, `--debug` flag, etc.) shipped between 0.1.0 and 0.2.0.

## [0.1.0] — Phase 1 MVP

### Added
- Headless dictation loop: Caps Lock → record → Whisper → paste.
- WASAPI capture with always-on stream and 500 ms pre-buffer.
- Caps Lock low-level hook with OS-level key suppression.
- Foreground-window tracker so paste targets the user's last real window.
- System tray with state-colored icon.
- Settings dialog (input device, paste method, language, model, vocabulary hint).
- Main window with status, last transcript, close-to-tray policy.
- Sleepy Productions violet theme.
