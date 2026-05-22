"""Pure function: ``HardwareProfile`` → ``(primary, runner_up)`` BackendConfigs.

Implements the decision matrix locked in the rearch plan:
- NVIDIA → faster-whisper / CUDA, model size scaled to VRAM.
- AMD Radeon RX → DirectML (Phase 2 backend; Phase 1 falls back to Moonshine
  so AMD users aren't blocked on Phase 2 landing).
- Intel Arc + iGPU → whisper.cpp SYCL (Phase 2; Phase 1 fallback to Moonshine).
- CPU-only → Moonshine Small.

No side effects, no I/O, no logging beyond DEBUG. The wizard calls this and
shows the result to the user with a "Show alternatives" expander.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import BackendConfig
from .probe import GpuInfo, GpuVendor, HardwareProfile

# Phase-1 backends ship in the base wheel. Phase-2 backends (directml,
# whispercpp_*) need their wheels installed by the wizard before use.
# In Phase 1 we transparently substitute the Moonshine CPU path for
# AMD/Intel/CPU users — they get a working dictation experience while
# the GPU backends are still being built — and the wizard records the
# original GPU recommendation in ``cfg.extra['preferred']`` so we can
# upgrade them automatically when Phase 2 lands.
PHASE_1_BACKENDS = {"cuda_ct2", "moonshine"}


@dataclass
class Recommendation:
    """What the wizard shows on the recommendation screen."""

    primary: BackendConfig
    runner_up: BackendConfig | None = None
    reason: str = ""  # one-line human description, shown under the card


def recommend(profile: HardwareProfile, *, phase1_only: bool = True) -> Recommendation:
    """Pick a backend for this machine.

    ``phase1_only=True`` substitutes Moonshine for any Phase-2 backend.
    Flip to False once AMD + Intel ship in Phase 2.
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
            extra={"reason": f"AMD {_short_name(gpu.name)}"},
        )
        primary = _moonshine_for(profile) if phase1_only else ideal
        runner_up = _moonshine_for(profile)
        return Recommendation(
            primary=primary,
            runner_up=runner_up,
            reason=(
                f"AMD {_short_name(gpu.name)} detected. "
                + (
                    "DirectML backend ships in Phase 2 — running Moonshine on "
                    "CPU for now (~150-300 ms latency). "
                    "Your GPU will be used automatically once Phase 2 lands."
                    if phase1_only
                    else "Whisper via ONNX DirectML on your Radeon."
                )
            ),
        )

    if gpu.vendor == GpuVendor.INTEL:
        ideal = BackendConfig(
            name="whispercpp_sycl",
            model="small.en-q5_k_m",
            threads=4,
            extra={"reason": f"Intel {_short_name(gpu.name)}"},
        )
        primary = _moonshine_for(profile) if phase1_only else ideal
        runner_up = _moonshine_for(profile)
        return Recommendation(
            primary=primary,
            runner_up=runner_up,
            reason=(
                f"Intel {_short_name(gpu.name)} detected. "
                + (
                    "SYCL backend ships in Phase 2 — running Moonshine on "
                    "CPU for now (~150-300 ms latency). "
                    "Your GPU will be used automatically once Phase 2 lands."
                    if phase1_only
                    else "Whisper.cpp via Intel SYCL on your GPU."
                )
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
