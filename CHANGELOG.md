# Changelog

All notable changes to Slumbr are documented in this file. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and Slumbr's versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Slumbr is in pre-1.0; the public API and config format may shift between minor releases. The first 1.0 will lock both.

## [Unreleased]

### Added
- About dialog (reachable from the tray menu and the main window) showing version, brand, repo link, license.
- Footer on the main window surfacing version + Sleepy Productions branding.
- `--debug` flag for verbose logging.
- `.github/` scaffold — issue templates, PR template, lint workflow.
- `CONTRIBUTING.md`, `CHANGELOG.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`.
- Repository / issues / changelog URLs in `pyproject.toml`.

### Changed
- Replaced all `print()` calls with Python's `logging` module. Single formatter, level-based filtering.
- `_TranscribeWorker` moved out of `app.py` into `slumbr/stt/worker.py` for clarity.
- Main window buttons now have proper focus / hover / pressed states (accessibility).
- Settings dialog no longer hardcodes hex strings — all colors flow through `slumbr/theme.py`.
- Tray icon idle color bumped slightly for better contrast against light taskbars.
- README rewritten with badges, troubleshooting, "how it works", and known limitations.

### Removed
- Dead `slumbr/input/stream_paste.py` (a previous streaming-paste experiment that was superseded by popup-only partials).

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
