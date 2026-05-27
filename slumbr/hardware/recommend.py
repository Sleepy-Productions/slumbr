"""Pure function: ``HardwareProfile`` → ``(primary, runner_up)`` BackendConfigs.

Phase-2 decision matrix:
- NVIDIA discrete         → faster-whisper / CUDA, model size scaled to VRAM.
- AMD Radeon RX discrete  → DirectML + Whisper ONNX (DX12, vendor-neutral).
- Intel Arc discrete      → DirectML (SYCL deferred to Phase 2C — needs
                            a custom whisper.cpp DLL we don't ship yet).
- Intel iGPU              → DirectML when usable; falls back to Moonshine
                            CPU on weak iGPUs where DML is slower than CPU.
- CPU-only                → Moonshine Small (~150–300 ms — snappier than
                            whisper.cpp on CPU, and reuses the model that
                            already powers the popup partials).

No side effects, no I/O, no logging beyond DEBUG. The wizard calls this
and shows the result to the user with a "Show alternatives" expander.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import BackendConfig
from .probe import GpuInfo, GpuVendor, HardwareProfile


@dataclass
class Recommendation:
    """What the wizard shows on the recommendation screen."""

    primary: BackendConfig
    runner_up: BackendConfig | None = None
    reason: str = ""  # one-line human description, shown under the card


@dataclass
class Option:
    """One pick within a Settings → Engine dimension (backend / model /
    precision). Each dimension is tiered independently so the user can mix
    a high model with a light backend, etc. ``key`` is the tier slot;
    ``value`` is what gets written to ``BackendConfig``.
    """

    key: str  # "recommended" | "balanced" | "light"
    value: str  # backend name, model id, or compute_type string
    note: str  # one-line descriptor shown under the value


# Display text for each tier key (kept uniform across all three dimensions).
TIER_LABELS: dict[str, str] = {
    "recommended": "Recommended",
    "balanced": "Balanced",
    "light": "Light",
}


def hardware_summary(profile: HardwareProfile) -> tuple[str, str]:
    """Return ``(hardware_label, why)`` for the Engine panel header."""
    gpu = profile.best_gpu
    label = _hardware_label(profile)
    cpu_note = " Your CPU runs the live preview + punctuation in parallel."
    if gpu is not None and gpu.vendor == GpuVendor.NVIDIA and gpu.is_discrete:
        why = (
            f"GPU + CPU: the {_short_name(gpu.name)} (CUDA) does the final transcribe — "
            f"fastest, most accurate, sized to your {gpu.vram_gb:.0f} GB of VRAM.{cpu_note}"
        )
    elif gpu is not None and gpu.vendor in (GpuVendor.AMD, GpuVendor.INTEL):
        vendor = "AMD Radeon" if gpu.vendor == GpuVendor.AMD else "Intel"
        why = (
            f"GPU + CPU: your {vendor} (DirectML, DX12, zero-config) does the final "
            f"transcribe.{cpu_note}"
        )
    else:
        why = "CPU only — Moonshine runs snappily on your processor (no GPU path detected)."
    return label, why


def hardware_rows(profile: HardwareProfile) -> list[tuple[str, str, str]]:
    """Detected-hardware rows for the Engine header: ``(dimension, value,
    role)``. Always surfaces BOTH the GPU (when one is present) and the CPU,
    each on its own line with the job it does — so it's unmistakable that
    Slumbr detected the CPU too, not just the obvious GPU.
    """
    rows: list[tuple[str, str, str]] = []
    gpu = profile.best_gpu

    if gpu is not None and gpu.vendor == GpuVendor.NVIDIA and gpu.is_discrete:
        rows.append(("GPU", _gpu_value(gpu), "CUDA · final transcribe"))
    elif gpu is not None and gpu.vendor in (GpuVendor.AMD, GpuVendor.INTEL):
        rows.append(("GPU", _gpu_value(gpu), "DirectML (DX12) · final transcribe"))

    cpu = _short_name(profile.cpu_brand) if profile.cpu_brand else "Detected (name unavailable)"
    if rows:  # a GPU does the heavy lifting → CPU runs the live layer
        rows.append(("CPU", cpu, "Live preview · VAD · punctuation"))
    else:  # CPU-only machine → CPU does everything
        rows.append(("CPU", cpu, "Moonshine · final transcribe + live preview"))
    return rows


def _gpu_value(gpu: GpuInfo) -> str:
    val = _short_name(gpu.name)
    if gpu.vram_gb >= 1:
        val += f" · {gpu.vram_gb:.0f} GB"
    return val


def backend_options(profile: HardwareProfile) -> list[Option]:
    """Tiered backend picks for the detected hardware (heaviest → lightest)."""
    gpu = profile.best_gpu
    if gpu is not None and gpu.vendor == GpuVendor.NVIDIA and gpu.is_discrete:
        return [
            Option(
                "recommended", "cuda_ct2", "Faster-Whisper · CUDA — fastest on your NVIDIA GPU."
            ),
            Option("balanced", "directml", "DirectML · DX12 — vendor-neutral GPU path."),
            Option("light", "moonshine", "Moonshine · CPU — no GPU load, very snappy."),
        ]
    if gpu is not None and gpu.vendor in (GpuVendor.AMD, GpuVendor.INTEL):
        gpu_word = "Radeon" if gpu.vendor == GpuVendor.AMD else "Arc / Xe"
        return [
            Option("recommended", "directml", f"DirectML · DX12 — your {gpu_word} GPU."),
            Option("balanced", "cpu_ct2", "Faster-Whisper · CPU — higher accuracy, slower."),
            Option("light", "moonshine", "Moonshine · CPU — no GPU load."),
        ]
    # CPU-only: Moonshine is the snappy seamless default; cpu_ct2 is the
    # opt-in accuracy upgrade (real Whisper on CPU, no GPU required).
    return [
        Option("recommended", "moonshine", "Moonshine · CPU — snappy, fully local."),
        Option("balanced", "cpu_ct2", "Faster-Whisper · CPU — real Whisper accuracy, slower."),
    ]


# CUDA Whisper model ladder, heaviest→lightest. ``large-v3-turbo`` is the
# top pick (within ~1% WER of large-v3 but ~3x faster — the right call for
# seamless dictation), so plain ``large-v3`` is intentionally not offered.
_CUDA_LADDER: tuple[str, ...] = ("large-v3-turbo", "medium", "small", "base")
_CUDA_NOTES: dict[str, str] = {
    "large-v3-turbo": "Fast + near-best accuracy. The seamless default.",
    "medium": "More accurate than small, moderate VRAM.",
    "small": "Fast, low memory.",
    "base": "Smallest footprint — for low-VRAM cards.",
}
_TIER_KEYS: tuple[str, ...] = ("recommended", "balanced", "light")


def _cuda_ladder_start(vram_gb: float) -> int:
    """Index into ``_CUDA_LADDER`` of the heaviest model that fits the card.
    Conservative: a model offered here must actually load + decode without
    OOM, since the Engine tab lets the user pick the "Recommended" tier
    blind. Unknown VRAM (0) is treated as small-class to stay safe."""
    if vram_gb >= 7.5:
        return 0  # turbo
    if vram_gb >= 5:
        return 1  # medium
    if vram_gb >= 3:
        return 2  # small
    if vram_gb <= 0:
        return 2  # unknown — small is the safe, still-capable default
    return 3  # base (tiny cards)


def model_options(backend_name: str, profile: HardwareProfile) -> list[Option]:
    """Tiered model picks for a given backend + hardware. The CUDA ladder is
    VRAM-scaled so a small card never offers a model it can't run."""
    gpu = profile.best_gpu
    if backend_name == "cuda_ct2":
        vram = gpu.vram_gb if gpu else 0.0
        ladder = _CUDA_LADDER[_cuda_ladder_start(vram) :] or (_CUDA_LADDER[-1],)
        return [
            Option(_TIER_KEYS[min(i, 2)], model, _CUDA_NOTES[model])
            for i, model in enumerate(ladder[:3])
        ]
    if backend_name == "directml":
        strong = gpu is not None and gpu.is_discrete and gpu.vram_gb >= 7.5
        if strong:
            return [
                Option(
                    "recommended", "large-v3-turbo", "Fastest large-class accuracy on your GPU."
                ),
                Option("balanced", "medium", "More accurate, a bit heavier."),
                Option("light", "small", "Fastest, lowest memory."),
            ]
        return [
            Option("recommended", "small", "The sweet spot for this GPU's throughput."),
            Option("balanced", "medium", "More accurate, heavier."),
        ]
    if backend_name == "cpu_ct2":
        # CPU Whisper: small is the accuracy/speed sweet spot on a modern
        # CPU; medium is noticeably slower; base is the fast floor. No
        # large/turbo — they're too slow to feel seamless without a GPU.
        return [
            Option("recommended", "small", "Good accuracy, usable speed on CPU."),
            Option("balanced", "medium", "More accurate — noticeably slower on CPU."),
            Option("light", "base", "Fastest CPU Whisper, lightest."),
        ]
    if backend_name == "moonshine":
        return [
            Option("recommended", "moonshine-base-en-int8", "Most accurate CPU model (~180 MB)."),
            Option("light", "moonshine-tiny-en-int8", "Fastest, ~80 MB."),
        ]
    # whisper.cpp variants are still stubs — single placeholder.
    return [Option("recommended", "small.en-q5_k_m", "whisper.cpp small (q5_k_m).")]


def compute_options(backend_name: str) -> list[Option]:
    """Tiered compute-precision picks. Only ``cuda_ct2`` exposes this knob;
    every other backend bakes precision into the model, so the row is empty
    (the UI hides it).
    """
    if backend_name != "cuda_ct2":
        return []
    return [
        Option("recommended", "int8_float16", "Best accuracy/speed balance."),
        Option("balanced", "float16", "Most accurate, more VRAM."),
        Option("light", "int8", "Smallest VRAM footprint."),
    ]


def thread_budget(profile: HardwareProfile) -> int:
    """Public wrapper so the UI can size Moonshine threads when the user
    switches to a CPU backend mid-session."""
    return max(2, min(8, _cpu_thread_budget(profile)))


def _hardware_label(profile: HardwareProfile) -> str:
    """Compact one-liner: GPU (+VRAM) · CPU, for the panel header."""
    parts: list[str] = []
    gpu = profile.best_gpu
    if gpu is not None:
        g = _short_name(gpu.name)
        if gpu.vram_gb >= 1:
            g += f" · {gpu.vram_gb:.0f} GB"
        parts.append(g)
    if profile.cpu_brand:
        parts.append(_short_name(profile.cpu_brand))
    return "  ·  ".join(parts) if parts else "Hardware unknown"


def recommend(profile: HardwareProfile, *, phase1_only: bool = False) -> Recommendation:
    """Pick a backend for this machine.

    ``phase1_only=True`` (legacy, kept for tests + smoke runs) forces
    AMD / Intel GPU paths to fall back to Moonshine on CPU. Phase 2
    flips the default to False — DirectML is live.
    """
    gpu = profile.best_gpu

    if gpu is None:
        return Recommendation(
            primary=_moonshine_for(profile),
            reason="No usable GPU detected — running Moonshine on CPU (~150-300 ms latency).",
        )

    if gpu.vendor == GpuVendor.NVIDIA and gpu.is_discrete:
        primary = _whisper_for_nvidia(gpu)
        runner_up = _moonshine_for(profile)
        return Recommendation(
            primary=primary,
            runner_up=runner_up,
            reason=(
                f"NVIDIA {_short_name(gpu.name)} detected — Whisper "
                f"{primary.model} on CUDA for max accuracy. "
                f"Pick CPU/Moonshine in alternatives for battery life."
            ),
        )

    if gpu.vendor == GpuVendor.AMD and gpu.is_discrete:
        ideal = BackendConfig(
            name="directml",
            model=_whisper_model_for_vram(gpu.vram_bytes),
            compute_type="int8",
            extra={"detected_gpu": gpu.name, "detected_vram_gb": round(gpu.vram_gb, 2)},
        )
        primary = _moonshine_for(profile) if phase1_only else ideal
        runner_up = _moonshine_for(profile)
        return Recommendation(
            primary=primary,
            runner_up=runner_up,
            reason=(
                f"AMD {_short_name(gpu.name)} detected — Whisper {ideal.model} via "
                "ONNX Runtime DirectML on your Radeon. "
                "Pick CPU/Moonshine in alternatives for battery life."
                if not phase1_only
                else f"AMD {_short_name(gpu.name)} detected. Running Moonshine on CPU."
            ),
        )

    if gpu.vendor == GpuVendor.INTEL:
        # Intel discrete (Arc) AND iGPU both route through DirectML in
        # Phase 2. SYCL is the long-term Intel path but ships in Phase 2C
        # (needs a custom whisper.cpp DLL we don't bundle yet).
        # For very weak iGPUs (Intel UHD), DirectML's overhead can beat
        # the iGPU's tiny throughput — we still recommend it because the
        # Engine tab's runner-up lets the user opt down to Moonshine CPU.
        ideal = BackendConfig(
            name="directml",
            model=_intel_model_for(gpu),
            compute_type="int8",
            extra={"detected_gpu": gpu.name, "detected_vram_gb": round(gpu.vram_gb, 2)},
        )
        primary = _moonshine_for(profile) if phase1_only else ideal
        runner_up = _moonshine_for(profile)
        kind = "Arc" if gpu.is_discrete else "Xe / UHD iGPU"
        return Recommendation(
            primary=primary,
            runner_up=runner_up,
            reason=(
                f"Intel {kind} ({_short_name(gpu.name)}) detected — Whisper "
                f"{ideal.model} via ONNX Runtime DirectML."
                if not phase1_only
                else f"Intel {_short_name(gpu.name)} detected. Running Moonshine on CPU."
            ),
        )

    # Anything else (exotic vendor, or iGPU we don't have a path for yet).
    return Recommendation(
        primary=_moonshine_for(profile),
        reason=(
            f"GPU {_short_name(gpu.name)} doesn't match a known acceleration path — "
            "running Moonshine on CPU."
        ),
    )


# ---------------------------------------------------------- helpers


def _whisper_model_for_vram(vram_bytes: int) -> str:
    """Pick the seamless-best Whisper model that comfortably fits. Capped at
    ``large-v3-turbo`` (not plain large-v3): turbo is within ~1% WER but ~3x
    faster, which matters more for live dictation — and it keeps recommend()
    in lockstep with the Engine tab's VRAM-scaled model tiers (the
    ``_CUDA_LADDER``), so the default and the "Recommended" card never
    disagree."""
    return _CUDA_LADDER[_cuda_ladder_start(vram_bytes / (1024**3))]


def _ct2_compute_type_for_vram(vram_bytes: int) -> str:
    gb = vram_bytes / (1024**3)
    if gb >= 14:
        return "float16"
    if gb >= 7.5:
        return "int8_float16"
    return "int8"


def _intel_model_for(gpu: GpuInfo) -> str:
    """Intel discrete (Arc) gets the full small/medium tier; iGPUs are
    pinned to small to keep latency under ~1 s even on UHD/Xe shared
    memory. Users with beefier Arc cards can bump in Settings.
    """
    if gpu.is_discrete and gpu.vram_gb >= 6:
        return "medium"
    return "small"


def _whisper_for_nvidia(gpu: GpuInfo) -> BackendConfig:
    return BackendConfig(
        name="cuda_ct2",
        model=_whisper_model_for_vram(gpu.vram_bytes),
        compute_type=_ct2_compute_type_for_vram(gpu.vram_bytes),
        extra={"detected_gpu": gpu.name, "detected_vram_gb": round(gpu.vram_gb, 2)},
    )


def _moonshine_for(profile: HardwareProfile) -> BackendConfig:
    return BackendConfig(
        name="moonshine",
        model="moonshine-base-en-int8",
        threads=max(2, min(8, _cpu_thread_budget(profile))),
        extra={"detected_cpu": profile.cpu_brand},
    )


def _cpu_thread_budget(profile: HardwareProfile) -> int:
    """Conservative thread count for Moonshine. We don't want to peg
    every core on a dictation hotkey press; that fights the UI thread
    and the foreground app.
    """
    import os  # noqa: PLC0415

    total = os.cpu_count() or 4
    return max(2, total // 2)


def _short_name(name: str) -> str:
    """Compact a long Windows adapter name for the recommendation card.

    "NVIDIA GeForce RTX 4070 Laptop GPU" → "GeForce RTX 4070".
    """
    name = name.strip()
    for stripped in ("NVIDIA ", "AMD ", "Intel(R) ", "Intel "):
        if name.startswith(stripped):
            name = name[len(stripped) :]
            break
    # Drop registered/trademark marks wherever they sit ("Core(TM) i9" →
    # "Core i9"), then trailing fluff, then collapse any double spaces.
    name = name.replace("(R)", "").replace("(TM)", "")
    for tail in (" Laptop GPU", " Graphics"):
        if name.endswith(tail):
            name = name[: -len(tail)].rstrip()
    return " ".join(name.split())
