"""Slumbr persistent settings.

Stored at ``%APPDATA%\\Slumbr\\config.json``. Loaded into a ``SlumbrConfig``
dataclass at startup; the Settings dialog mutates the instance and calls
``save()`` on OK.

Persistence hardening (per the design doc):
- **Atomic writes:** write to ``config.json.tmp``, then ``os.replace()`` onto
  ``config.json``. We never leave a half-written file on disk.
- **Schema-tolerant load:** unknown keys are ignored, missing keys fall
  back to defaults — so adding a setting in a later version doesn't break
  reading old files, and reading newer files in older code still works.
- **Self-healing:** if the file is missing, empty, or malformed JSON, we
  log a warning, back the bad file up with a timestamp suffix, and write
  a fresh default. Never crash at startup over a bad config.

Schema (May 2026 rearch):
- ``backend: BackendConfig | None`` selects the STT engine and its
  model/precision. ``None`` means "the first-launch wizard hasn't run
  yet" — ``app.py`` runs the wizard before constructing any engine.
- Legacy top-level ``model_size`` + ``compute_type`` keys (pre-rearch)
  are auto-migrated into a ``BackendConfig(name='cuda_ct2', ...)`` on
  load so users coming from v0.1.0 don't see the wizard.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def _config_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "Slumbr"
    # Non-Windows fallback; we're Windows-only but keep the path robust.
    return Path.home() / ".slumbr"


CONFIG_DIR = _config_dir()
CONFIG_PATH = CONFIG_DIR / "config.json"


# ---------------------------------------------------------------- backend


@dataclass
class BackendConfig:
    """Engine selection + per-backend tuning.

    ``name`` is the discriminator the factory dispatches on:
      - ``cuda_ct2``        → faster-whisper / CTranslate2 (NVIDIA)
      - ``directml``        → ONNX Runtime DirectML (AMD/Intel; Phase 2)
      - ``whispercpp_sycl`` → whisper.cpp Intel SYCL build (Phase 2)
      - ``whispercpp_cpu``  → whisper.cpp CPU build (Phase 2 fallback)
      - ``moonshine``       → sherpa-onnx Moonshine offline (CPU primary)

    ``model`` is backend-specific (e.g. ``"large-v3-turbo"`` for ct2,
    ``"moonshine-base-en-int8"`` for moonshine, ``"small.en-q5_k_m"``
    for whisper.cpp). The factory + adapter know what to do with it.
    """

    name: str
    model: str
    compute_type: str | None = None    # ct2 only
    threads: int | None = None         # whispercpp / moonshine CPU thread count
    device_index: int = 0              # multi-GPU systems
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BackendConfig:
        known = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in known}
        # Defensive: 'extra' must be a dict if present.
        if "extra" in kwargs and not isinstance(kwargs["extra"], dict):
            kwargs["extra"] = {}
        return cls(**kwargs)


# ---------------------------------------------------------------- slumbr


@dataclass
class SlumbrConfig:
    # ----- STT backend (None = wizard hasn't run yet) -----
    backend: BackendConfig | None = None

    # ----- Mic + paste -----
    input_device_name: str | None = None
    auto_send: bool = False
    preserve_clipboard: bool = True
    # When True, Slumbr LEAVES the dictated text on your clipboard after
    # pasting so you can paste that exact message again (the next dictation
    # replaces it). When False, your previous clipboard contents are restored.
    keep_transcript_on_clipboard: bool = False
    # Neutral default with a faint violet hint — keeps a fresh install reading
    # clean / black-and-white. Stays mid-tone on purpose (the accent doubles as
    # the primary-button background under light text). Users pick a vivid accent
    # (e.g. Sleepy's #794fb5) in Settings → Customization.
    accent_color: str = "#7E7A92"
    paste_method: str = "ctrl_v"   # "ctrl_v" | "ctrl_shift_v" | "type"

    # ----- Hotkey -----
    # ``hotkey_vks`` is the combo: a list of 1–4 Windows VK codes that must
    # be held together to toggle dictation (e.g. [Ctrl, Shift, J]). A single
    # element behaves like the classic single-key tap (default Caps Lock).
    # ``hotkey_vk`` is kept as the legacy single-key field (= the combo's
    # trigger) so older code / configs round-trip; ``hotkey_vks`` is the
    # source of truth and ``from_dict`` migrates an old ``hotkey_vk`` into it.
    hotkey_vk: int = 0x14
    hotkey_vks: list[int] = field(default_factory=lambda: [0x14])

    # ----- ASR hot-tunable knobs -----
    language: str = "en"
    initial_prompt: str = ""

    # ----- Output cleanup -----
    # ``word_replacements`` is a find-replace map applied to the final
    # transcript before paste: {heard: corrected}. Whole-word, case-
    # insensitive. Fixes consistent mishears the model makes on the user's
    # jargon (e.g. "keybinde" -> "keybinds") and works on EVERY backend
    # since it's pure post-processing (unlike ``initial_prompt``, which only
    # biases Whisper). Empty by default — populated via Settings → Voice.
    word_replacements: dict[str, str] = field(default_factory=dict)
    # Strip Whisper's classic end-of-clip hallucinations ("Thank you.",
    # "Thanks for watching.") that it invents on trailing silence. Only the
    # curated phrase set in ``polish.py`` is removed, and only when real
    # content precedes it — so a genuine short "thank you" is left alone.
    strip_trailing_filler: bool = True

    # ----- Streaming engine experiment toggle -----
    streaming_visual_leading_edge: bool = False

    # ----- Reverse PTT (mute external apps while dictating) -----
    # When enabled and ``reverse_ptt_vk`` is non-zero, Slumbr presses
    # that VK key for the duration of every dictation session. The user
    # configures the same keybind in their call app's "Push To Mute"
    # (Discord) / equivalent. This is a workaround: Windows has no
    # per-app mic mute API, so we lean on the other app's own mute
    # keybind.
    reverse_ptt_enabled: bool = False
    reverse_ptt_vk: int = 0

    # ----- Recording popup look -----
    # ``compact_popup=True`` strips the popup down to just the audio
    # visualizer — no status dot, no elapsed-time label, no live
    # partial-transcript panel. For users who find the live word
    # preview distracting and just want a glanceable "yes mic is live"
    # indicator.
    compact_popup: bool = False
    # ``popup_follow_cursor=True`` re-anchors the popup to the cursor
    # at 60 Hz while visible. Default off because moving the mouse
    # over a terminal with xterm mouse-tracking enabled (Windows
    # Terminal, conhost, Claude Code) makes the terminal echo mouse-
    # motion escape codes into stdin — which then appear as garbage
    # text in any pasted transcript that lands there.
    popup_follow_cursor: bool = False

    # ----- Virtual mic routing (universal reverse-PTT, Phase 3) -----
    # Slumbr passes the real-mic audio through a virtual cable device
    # (VB-Audio Virtual Cable or similar). Call apps are pointed at the
    # virtual cable as their mic input; during dictation Slumbr writes
    # silence into the cable so other apps hear nothing while Slumbr
    # keeps reading the real mic for transcription. Works in every app
    # (Zoom / Teams / OBS / browser calls), not just Discord. Requires
    # a one-time VB-Cable install from vb-audio.com.
    mic_routing_enabled: bool = False
    mic_routing_device_name: str = ""

    # ----- Window close policy (vestigial — the hub window is gone in
    # the May 2026 rearch, but the field remains so older configs load
    # cleanly and downstream code can read it without KeyError).
    close_to_tray: bool = True
    close_choice_made: bool = True

    # ---------------------------------------------------------- (de)serialize
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SlumbrConfig:
        """Tolerant constructor with legacy-schema migration.

        Pre-rearch configs had top-level ``model_size`` / ``compute_type``
        fields. We rebuild a ``BackendConfig(name='cuda_ct2', ...)``
        from those so legacy users skip the first-launch wizard.
        """
        known = {f.name for f in fields(cls)}
        kwargs: dict[str, Any] = {k: v for k, v in data.items() if k in known}

        # ----- hotkey combo migration: old configs only have ``hotkey_vk``.
        # Build ``hotkey_vks`` from it when absent so single-key binds survive
        # the upgrade; keep the two in sync (trigger = last element).
        raw_vks = data.get("hotkey_vks")
        if isinstance(raw_vks, list) and raw_vks:
            vks = [int(v) for v in raw_vks if isinstance(v, (int, float))]
            kwargs["hotkey_vks"] = vks or [0x14]
        elif "hotkey_vk" in data:
            kwargs["hotkey_vks"] = [int(data["hotkey_vk"])]
        if kwargs.get("hotkey_vks"):
            kwargs["hotkey_vk"] = kwargs["hotkey_vks"][-1]

        # ----- backend field
        raw_backend = data.get("backend")
        if isinstance(raw_backend, dict):
            try:
                kwargs["backend"] = BackendConfig.from_dict(raw_backend)
            except (TypeError, KeyError) as e:
                log.warning("backend block malformed (%s) — wizard will re-prompt", e)
                kwargs["backend"] = None

        # ----- legacy migration: model_size + compute_type at top level
        if kwargs.get("backend") is None and "model_size" in data:
            legacy_model = str(data.get("model_size") or "large-v3-turbo")
            legacy_ct = str(data.get("compute_type") or "int8_float16")
            log.info(
                "migrating legacy config to BackendConfig(cuda_ct2, %s, %s)",
                legacy_model, legacy_ct,
            )
            kwargs["backend"] = BackendConfig(
                name="cuda_ct2",
                model=legacy_model,
                compute_type=legacy_ct,
            )

        # ----- output cleanup: coerce word_replacements to a clean {str:str}
        raw_repl = data.get("word_replacements")
        if isinstance(raw_repl, dict):
            kwargs["word_replacements"] = {
                str(k): str(v)
                for k, v in raw_repl.items()
                if str(k).strip() and isinstance(v, (str, int, float))
            }
        elif "word_replacements" in kwargs:
            # Present but not a dict (corrupt/hand-edited) — drop it so the
            # empty-map default is used instead of a bad type.
            del kwargs["word_replacements"]

        return cls(**kwargs)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    # ----------------------------------------------------------------- I/O
    @classmethod
    def load(cls) -> SlumbrConfig:
        if not CONFIG_PATH.exists():
            cfg = cls()
            cfg.save()
            return cfg
        try:
            text = CONFIG_PATH.read_text(encoding="utf-8")
            if not text.strip():
                raise ValueError("empty config file")
            data = json.loads(text)
            return cls.from_dict(data)
        except Exception as e:  # noqa: BLE001
            log.warning("could not load %s: %s; backing up + resetting", CONFIG_PATH, e)
            try:
                backup = CONFIG_PATH.with_name(f"config.json.corrupt-{int(time.time())}")
                shutil.copy2(CONFIG_PATH, backup)
            except Exception as e2:  # noqa: BLE001
                log.warning("backup failed (continuing anyway): %s", e2)
            cfg = cls()
            cfg.save()
            return cfg

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp, CONFIG_PATH)
