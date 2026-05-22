"""Slumbr persistent settings.

Stored at `%APPDATA%\\Slumbr\\config.json`. Loaded into a `SlumbrConfig`
dataclass at startup; the Settings dialog mutates the instance and calls
`save()` on OK.

Persistence hardening (per the design doc):
- **Atomic writes:** write to `config.json.tmp`, then `os.replace()` onto
  `config.json`. We never leave a half-written file on disk.
- **Schema-tolerant load:** unknown keys are ignored, missing keys fall
  back to defaults — so adding a setting in a later version doesn't break
  reading old files, and reading newer files in older code still works.
- **Self-healing:** if the file is missing, empty, or malformed JSON, we
  log a warning, back the bad file up with a timestamp suffix, and write
  a fresh default. Never crash at startup over a bad config.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from dataclasses import asdict, dataclass, fields
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


@dataclass
class SlumbrConfig:
    # Mic input — sounddevice device name substring or numeric index, or
    # None to use the system default. We store the *name* by default so
    # the choice survives the device-index reshuffle that happens on
    # USB-mic hot-plug.
    input_device_name: str | None = None

    # Reserved for follow-up turns — wired through but not yet UI-bound.
    auto_send: bool = False
    preserve_clipboard: bool = True
    accent_color: str = "#9B6FE0"  # Infinity Board violet[3]

    # How transcribed text is sent into the focused window.
    # - "ctrl_v"        : clipboard + Ctrl+V. Fastest. Works for chats,
    #                     browsers, editors. Does NOT work in most
    #                     terminals (Ctrl+V is a literal-char prefix).
    # - "ctrl_shift_v"  : clipboard + Ctrl+Shift+V. Required for VS Code's
    #                     integrated terminal, Windows Terminal, conhost.
    #                     Still works in chats/browsers (they treat it as
    #                     "paste as plain text", which is what we have).
    # - "type"          : type each character. Universal — works anywhere
    #                     keystrokes land. Slower for long messages and
    #                     bypasses the clipboard entirely.
    paste_method: str = "ctrl_v"

    # Global dictation hotkey. Stored as a raw Windows Virtual-Key code so
    # we don't have to maintain a name<->code table — the UI picker writes
    # the int directly. Default 0x14 = Caps Lock.
    hotkey_vk: int = 0x14

    # ASR config. Two knobs that make the biggest difference for accuracy
    # on uncommon words:
    #
    # - `language`: pinning to "en" skips Whisper's auto-detect (saves
    #   ~50 ms per utterance) and avoids the rare case where short
    #   utterances get mis-routed to e.g. Welsh or Dutch and decode badly.
    #   Set to "" if you want to dictate in multiple languages.
    # - `initial_prompt`: a free-form hint string passed to Whisper as
    #   prior-context. The decoder is heavily biased toward words that
    #   appear here, so listing proper nouns / technical terms / slang
    #   that matter to you dramatically reduces miss-rate on those words.
    #   Keep it under ~200 tokens; Whisper truncates longer prompts.
    language: str = "en"
    initial_prompt: str = ""

    # Whisper model size + numeric precision. Both load-time only — changes
    # take effect on the next app restart, since faster-whisper can't
    # hot-swap a model.
    #
    # - "large-v3"        : most accurate. ~3 GB. Best for rare words.
    # - "large-v3-turbo"  : distilled v3, faster, slightly less accurate.
    # - "distil-large-v3" : middle ground.
    # - "medium" / "small": progressively smaller and worse, only if VRAM
    #                       is constrained.
    #
    # `compute_type` controls precision on the GPU:
    # - "int8_float16"   : INT8 weights with FP16 activations. Best
    #                       accuracy/speed trade on modern Nvidia.
    # - "int8"           : pure INT8. Smallest VRAM, slight accuracy hit.
    # - "float16"        : full FP16. Best accuracy, largest VRAM.
    model_size: str = "large-v3-turbo"
    compute_type: str = "int8_float16"

    # Experimental: alongside Moonshine + LA-2 (which owns committed
    # text), run a sherpa-onnx streaming Zipformer to drive the
    # uncommitted tail chars in the popup. Faster token emission (~50 ms
    # vs Moonshine's ~200 ms cadence) but the only available English
    # streaming Zipformer is LibriSpeech-trained, so the visual tail can
    # be wrong on conversational speech. Off by default until A/B'd
    # subjectively.
    streaming_visual_leading_edge: bool = False

    # Main-window close behavior. True = minimize to tray on close (user can
    # quit explicitly from the tray menu or the in-window Quit button).
    # False = closing the window exits the app entirely.
    close_to_tray: bool = True
    # When False, the next window-close shows a one-time dialog asking the
    # user to pick close-to-tray vs quit, then flips this to True.
    close_choice_made: bool = False

    # ---------------------------------------------------------- (de)serialize
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SlumbrConfig:
        """Tolerant constructor: ignores unknown keys, defaults missing ones."""
        known = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in known}
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
