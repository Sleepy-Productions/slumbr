"""Slumbr accuracy + speed benchmark harness.

Measures **WER** (word error rate) and **RTF** (real-time factor = decode
time / audio length) across backends + models on a local set of clips, so
engine/decode choices get tuned with numbers instead of vibes.

No new dependencies: WAV I/O via stdlib ``wave``, WER computed here, audio
capture via the already-shipped ``sounddevice``.

Workflow
--------
1. Build a test set from YOUR voice (most representative, no download)::

       python scripts/benchmark.py record

   Reads prompts from ``scripts/benchmark_prompts.txt``; for each it records
   you reading it and saves ``<n>.wav`` + ``<n>.txt`` under the data dir.
   (You can also drop in your own ``name.wav`` (16 kHz mono) + ``name.txt``
   reference pairs by hand.)

2. Measure every config against the clips::

       python scripts/benchmark.py run
       python scripts/benchmark.py run --only "cuda turbo,cpu small"

   Prints a table of avg WER% and avg RTF per config and writes
   ``results.json`` next to the clips.

WER is computed on normalized text (lowercased, punctuation stripped),
which is the standard, model-fair comparison — capitalization/punctuation
differences don't count against a backend.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import wave
from pathlib import Path

import numpy as np

# slumbr is installed editable, so these resolve from anywhere.
from slumbr.config import BackendConfig
from slumbr.stt.factory import build_transcriber

_HERE = Path(__file__).resolve().parent
DATA_DIR = _HERE / "benchmark_data"
PROMPTS_FILE = _HERE / "benchmark_prompts.txt"
SAMPLE_RATE = 16000


# --------------------------------------------------------------------- WER

_PUNCT = re.compile(r"[^\w\s]")


def _normalize(text: str) -> list[str]:
    """Lowercase, strip punctuation, split into words — standard WER prep."""
    return _PUNCT.sub(" ", text.lower()).split()


def _wer(ref_words: list[str], hyp_words: list[str]) -> float:
    """Word error rate = word-level Levenshtein(ref, hyp) / len(ref)."""
    n, m = len(ref_words), len(hyp_words)
    if n == 0:
        return 0.0 if m == 0 else 1.0
    dp = list(range(m + 1))
    for i in range(1, n + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, m + 1):
            cur = dp[j]
            cost = 0 if ref_words[i - 1] == hyp_words[j - 1] else 1
            dp[j] = min(dp[j] + 1, dp[j - 1] + 1, prev + cost)
            prev = cur
    return dp[m] / n


# ------------------------------------------------------------------- WAV I/O


def _load_wav(path: Path) -> np.ndarray:
    """Load a WAV as float32 mono @ 16 kHz. Averages stereo, linear-resamples
    if the file isn't already 16 kHz (good enough for a benchmark)."""
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        raw = w.readframes(w.getnframes())
    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    if sr != SAMPLE_RATE and data.size:
        new_n = int(len(data) * SAMPLE_RATE / sr)
        data = np.interp(
            np.linspace(0, len(data), new_n, endpoint=False),
            np.arange(len(data)),
            data,
        ).astype(np.float32)
    return data.astype(np.float32)


def _save_wav(path: Path, samples: np.ndarray) -> None:
    pcm = np.clip(samples * 32768.0, -32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.tobytes())


# ------------------------------------------------------------------- configs

# (label, BackendConfig). cuda_* need an NVIDIA GPU; cpu_*/moonshine run
# anywhere. Edit freely — this is the matrix the harness compares.
def _default_configs() -> list[tuple[str, BackendConfig]]:
    return [
        ("cuda turbo", BackendConfig("cuda_ct2", "large-v3-turbo", compute_type="int8_float16")),
        ("cuda medium", BackendConfig("cuda_ct2", "medium", compute_type="int8_float16")),
        ("cuda small", BackendConfig("cuda_ct2", "small", compute_type="int8_float16")),
        ("moonshine base", BackendConfig("moonshine", "moonshine-base-en-int8")),
        ("cpu small", BackendConfig("cpu_ct2", "small")),
    ]


# --------------------------------------------------------------------- clips


def _load_clips(data_dir: Path) -> list[tuple[str, np.ndarray, str]]:
    """Return ``[(name, audio, reference), …]`` for every .wav with a .txt."""
    clips: list[tuple[str, np.ndarray, str]] = []
    for wav in sorted(data_dir.glob("*.wav")):
        ref_file = wav.with_suffix(".txt")
        if not ref_file.is_file():
            print(f"  (skip {wav.name}: no matching .txt reference)")
            continue
        ref = ref_file.read_text(encoding="utf-8").strip()
        clips.append((wav.stem, _load_wav(wav), ref))
    return clips


# ----------------------------------------------------------------- commands


def cmd_record(_args: argparse.Namespace) -> int:
    import sounddevice as sd  # noqa: PLC0415

    if not PROMPTS_FILE.is_file():
        print(f"No prompts file at {PROMPTS_FILE}")
        return 1
    prompts = [
        ln.strip()
        for ln in PROMPTS_FILE.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.startswith("#")
    ]
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Recording {len(prompts)} prompts into {DATA_DIR}")
    print("Read each line aloud the way you'd dictate. Ctrl+C to stop early.\n")
    for i, prompt in enumerate(prompts, 1):
        name = f"{i:02d}"
        wav_path = DATA_DIR / f"{name}.wav"
        if wav_path.exists():
            print(f"[{i:02d}] already recorded — skipping")
            continue
        print(f"[{i:02d}] “{prompt}”")
        try:
            input("     [Enter] to START …")
            chunks: list[np.ndarray] = []

            def _cb(indata, _frames, _t, _status, _sink=chunks) -> None:
                _sink.append(indata.copy())

            with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=_cb):
                input("     recording… [Enter] to STOP")
        except KeyboardInterrupt:
            print("\nstopped.")
            break
        if not chunks:
            print("     (no audio captured — skipping)")
            continue
        audio = np.concatenate(chunks).reshape(-1).astype(np.float32)
        _save_wav(wav_path, audio)
        (DATA_DIR / f"{name}.txt").write_text(prompt, encoding="utf-8")
        print(f"     saved {wav_path.name} ({len(audio) / SAMPLE_RATE:.1f}s)\n")
    print("Done. Now run:  python scripts/benchmark.py run")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    data_dir = Path(args.data) if args.data else DATA_DIR
    clips = _load_clips(data_dir)
    if not clips:
        print(f"No clips in {data_dir}. Run 'record' first (or drop in wav+txt pairs).")
        return 1
    total_audio = sum(len(a) for _, a, _ in clips) / SAMPLE_RATE
    print(f"Loaded {len(clips)} clips ({total_audio:.1f}s total) from {data_dir}\n")

    configs = _default_configs()
    if args.only:
        wanted = {s.strip() for s in args.only.split(",")}
        configs = [(lbl, cfg) for lbl, cfg in configs if lbl in wanted]
        if not configs:
            print(f"--only {args.only!r} matched no configs. Available: "
                  f"{[lbl for lbl, _ in _default_configs()]}")
            return 1

    results: list[dict] = []
    for label, cfg in configs:
        print(f"=== {label}  ({cfg.name} / {cfg.model}) ===")
        try:
            tr = build_transcriber(cfg, language="en", initial_prompt="")
            tr.warm_up()
        except Exception as e:  # noqa: BLE001
            print(f"  SKIP — could not build/warm: {e}\n")
            continue
        wers: list[float] = []
        rtfs: list[float] = []
        for name, audio, ref in clips:
            audio_s = max(len(audio) / SAMPLE_RATE, 1e-6)
            t0 = time.monotonic()
            try:
                hyp = tr.transcribe(audio)
            except Exception as e:  # noqa: BLE001
                print(f"  {name}: transcribe failed: {e}")
                continue
            dt = time.monotonic() - t0
            wer = _wer(_normalize(ref), _normalize(hyp))
            wers.append(wer)
            rtfs.append(dt / audio_s)
            print(f"  {name}: WER {wer * 100:5.1f}%  RTF {dt / audio_s:.3f}  | {hyp!r}")
        if hasattr(tr, "close"):
            try:
                tr.close()
            except Exception:  # noqa: BLE001, S110
                pass
        if wers:
            results.append({
                "label": label,
                "backend": cfg.name,
                "model": cfg.model,
                "clips": len(wers),
                "avg_wer_pct": round(float(np.mean(wers)) * 100, 2),
                "avg_rtf": round(float(np.mean(rtfs)), 3),
            })
        print()

    # Summary table, sorted by accuracy (lowest WER first).
    results.sort(key=lambda r: r["avg_wer_pct"])
    print("=" * 58)
    print(f"{'config':18} {'clips':>5} {'avg WER':>9} {'avg RTF':>9}")
    print("-" * 58)
    for r in results:
        print(f"{r['label']:18} {r['clips']:>5} {r['avg_wer_pct']:>8.1f}% {r['avg_rtf']:>9.3f}")
    print("=" * 58)
    print("WER = lower is better (accuracy). RTF = lower is faster "
          "(<1 means faster than real time).")

    out = data_dir / "results.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Slumbr accuracy/speed benchmark.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("record", help="record clips from your voice using the prompts file")
    run = sub.add_parser("run", help="measure WER + RTF across configs")
    run.add_argument("--only", help="comma-separated config labels to run (default: all)")
    run.add_argument("--data", help="clip directory (default: scripts/benchmark_data)")
    args = ap.parse_args()
    if args.cmd == "record":
        return cmd_record(args)
    return cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
