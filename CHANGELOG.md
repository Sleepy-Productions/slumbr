# Changelog

All notable changes to Slumbr are documented in this file. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and Slumbr's versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Slumbr follows SemVer: **MAJOR** for breaking changes (config format / behavior), **MINOR** for new features, **PATCH** for fixes.

## [Unreleased]

## [1.1.1] — 2026-06-13

A maintenance point-release: correctness and robustness fixes found in a post-tag code review. No new features; safe drop-in over 1.1.0.

### Fixed
- **History persistence no longer wiped on restart.** With *Keep history across restarts* enabled, quitting and reopening now merges the in-memory and on-disk transcripts instead of clearing the store, so saved history survives a restart.
- **Thread-safe history.** Every read and write of the history buffer is now guarded by a lock, closing a race where a transcript landing mid-rotation could be silently lost.
- **Mic-mirror resilience.** The virtual-cable mirror now reopens after an audio-device error and is guarded against being torn down mid-dictation by a concurrent Settings device change.
- **Bootstrap install on paths with spaces.** The editable-install command is now built as a proper argument list, so installing from a repo path containing spaces no longer splits the path.
- **Atomic state + safer shutdown.** The transcriber waits for its worker before closing (avoids a native use-after-free), and the history store quarantines a corrupt database and survives Windows file locks.
- **Runaway-repetition collapse** thresholds pinned with regression tests to guard against silent future drift.

## [1.1.0] — 2026-05-29

Post-launch round addressing external review feedback: smarter history, an opt-in to keep it, looser dependency pins, real CI, and an accuracy tier for strong GPUs.

### Added
- **Opt-in persistent history.** Settings → History → *Keep history across restarts* saves transcripts to an unencrypted SQLite file at `%APPDATA%\Slumbr\history.db` so they survive a restart. Off by default; turning it back off deletes the file (the ephemeral-by-default privacy story is preserved).
- **Full `large-v3` accuracy tier on high-VRAM NVIDIA GPUs (≥10 GB).** The most accurate Whisper model, offered as the top Engine pick where it still decodes in ~1 s; mid-range cards keep `large-v3-turbo` as the seamless default.
- CI now runs the test suite on Windows across Python 3.10–3.12 on every push/PR (was lint-only).
- README: a "Why Slumbr?" comparison, a vocabulary-hint explainer, and documented transcription-failure behavior; CONTRIBUTING: an installer-build section noting the pinned PyInstaller version.

### Changed
- **History is now a rolling window of the latest 200** (was 50): past the cap the *oldest* entry drops instead of the whole list being wiped, so recent dictations are always kept.
- Loosened exact dependency pins (`sounddevice`, `pynput`, `PySide6`) to tested floors and added a `huggingface_hub>=0.20` floor; added `pytest-mock` + `coverage` to the `dev` extra.

### Fixed
- README "no admin required" now flags the optional VB-Cable / reverse-PTT exception.

## [1.0.0] — 2026-05-27

The **1.0 public launch.** One-click installers that run on any PC, a reorganized Settings UI, an in-memory ephemeral History, proper Windows app identity (no more "pins as Python"), and a pile of polish + correctness fixes.

### Added
- **Offline first run for the CPU build — speech models ship inside the installer.** The CPU build bundles Moonshine + Silero-VAD + punctuation, so a fresh machine transcribes on first launch with no download and no network. The NVIDIA build bundles the same trio (so popup partials and the CPU fallback are instant offline) but downloads the GPU Whisper model once on first launch — bundling it too would push the installer past GitHub's 2 GiB release cap. The model loaders resolve any bundled copy before a Hugging Face fetch, falling back to download otherwise (and source/dev installs are unchanged).
- **In-memory, ephemeral History.** Your recent transcripts are held in memory only (latest 50); at the cap the list clears and starts fresh. Nothing about your dictations is written to disk — no history file, no session logs, no crash dumps — and it's all gone the moment you close Slumbr. Copy any line out (or all) while it's there.
- **Uninstaller cleans up after itself** — uninstalling offers to remove your `%APPDATA%\Slumbr` data (settings + downloaded Moonshine models), not just the program files.
- **Advanced Settings tab** — virtual-cable picker + installer, auto-send, "keep transcript on clipboard", and the vocabulary hint, moved out of the everyday surfaces.
- **Copy from History** — per row (double-click / right-click / Ctrl+C) and "Copy all".
- **Start Menu shortcut** alongside the desktop one, so Slumbr is findable in Start search.
- **Bundled house fonts** Inter + Sora (SIL OFL-1.1; licenses included under `slumbr/assets/fonts/`).

### Changed
- **Settings is now a grouped left-sidebar** (Setup / Preferences / Info) instead of top tabs, restyled on a house design system (Inter/Sora, 8pt spacing, one radius scale, disabled states, fade-in). Opens centered on launch.
- **History is in-memory and capped at 50** — it never touches disk and clears at the cap (and on close), so nothing about your dictations persists. The debug log records events/errors but no transcript text.
- **Windows app identity (AUMID).** Set on the process and on both shortcuts, so the taskbar button, pinning, jump list, and Start all read "Slumbr" instead of the host `pythonw.exe` / "Python".
- **Brand mark is fixed monochrome white** everywhere (shell icon, taskbar, About logo) — it never follows the user's accent.
- **Ships as one-click installers** — a universal **CPU build** that runs on any x64 Windows PC (bundles its models → offline first run, see Added), plus an **NVIDIA build** for GPU-accelerated dictation (downloads the GPU model once on first launch). The source install (`install.ps1` from a clone, which auto-detects hardware and installs the matching backend) is still fully supported.

### Fixed
- **Recurring "pink desktop icon."** `install.ps1` now busts the Windows shell icon cache (drop the cache DBs + `ie4uinit -show` + `SHChangeNotify`) so a stale tinted icon can't linger — the baked `.ico` and all runtime icons were already white.
- **White-on-white accent contrast.** Accent-filled controls compute a contrast-aware text color, so a light/white accent gets dark text instead of vanishing.
- **Paste-method labels** corrected — Ctrl+V default, Ctrl+Shift+V fallback, "type each character" for clipboard-hostile apps.
- Tray menu drops the stale "Reverse PTT (Discord)" item.
- **No more hard-exit when a GPU backend can't load.** If the chosen engine fails to build or warm up on a machine (driver mismatch, missing wheels, out-of-memory), Slumbr falls back to the bundled Moonshine CPU engine and keeps working instead of quitting.
- **Frozen builds guide you to the right download.** If a packaged build doesn't include your hardware's GPU backend, the first-launch wizard runs the bundled CPU engine and points you at the matching build — instead of attempting an in-app install that can't work in a packaged app.
- **Startup `mic_mirror` race** that logged a harmless error on every launch is closed.
- Declared supported Python as **3.10–3.12** in metadata (onnxruntime / sherpa-onnx wheels lag on 3.13).

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
