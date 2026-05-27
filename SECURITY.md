# Security Policy

## Supported versions

Slumbr follows Semantic Versioning. The latest 1.x release is supported with security fixes.

## Reporting a vulnerability

Slumbr is fully offline at runtime — no network calls, no remote endpoints, no auth — so most categories of vulnerability don't apply. The threats we care about are:

- **Dependency supply chain** (faster-whisper, sherpa-onnx, PySide6, pynput, pyperclip, sounddevice, pystray, Pillow, huggingface_hub, ctranslate2, numpy). If you discover a CVE in any of these affecting Slumbr's usage, please flag it.
- **The first-launch model download.** Slumbr pulls Whisper and the Moonshine streaming model from Hugging Face on first run. If you find a path that lets a third party redirect that download, that's a vulnerability.
- **Audio handling.** Slumbr keeps audio in RAM only and discards it after transcription. If you find a path that persists audio to disk unexpectedly, that's a privacy bug we want to know about.

To report, please **open a private security advisory** on the GitHub repository (Security → Report a vulnerability). If you prefer email, use the address listed on the maintainer's GitHub profile. We will acknowledge receipt within 7 days and aim to address confirmed issues within 30.

Please **don't** open a public issue for security reports — give us a chance to ship a fix first.
