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
    """Pick the largest Whisper model that comfortably fits."""
    gb = vram_bytes / (1024**3)
    if gb == 0:
        # VRAM unknown — pick the safe middle option.
        return "small"
    if gb >= 14:
        return "large-v3"
    if gb >= 7.5:
        return "large-v3-turbo"
    if gb >= 5.5:
        return "small"
    if gb >= 3.5:
        return "small"
    return "base"


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
            name = name[len(stripped):]
            break
    # Trim trailing fluff like "(R)", "Graphics", "Laptop GPU".
    for tail in (" Laptop GPU", " Graphics", "(R)", "(TM)"):
        if name.endswith(tail):
            name = name[: -len(tail)].rstrip()
    return name
