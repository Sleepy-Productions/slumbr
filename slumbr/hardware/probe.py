"""Hardware probe — snapshot the current Windows machine.

Two-stage strategy (per the rearch plan + Plan-agent risk callout):

1. **Primary:** PowerShell ``Get-CimInstance Win32_VideoController`` via
   subprocess with a 3-second timeout. Returns adapter name + VRAM for
   every video device. Fast and Win10/11-compatible.
2. **Fallback:** Read the video adapter registry key directly via
   ``winreg``. Used when WMI is broken (stale repository, hung WinMgmt
   service). Slower but doesn't require WMI to be healthy.
3. **Last resort:** Assume CPU-only.

The probe is intentionally conservative — we'd rather ship the user a
CPU backend that works than a GPU backend that crashes. The wizard's
"Show alternatives" link lets them override the recommendation if they
know more than we do about their hardware.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

log = logging.getLogger(__name__)


class GpuVendor(str, Enum):
    NVIDIA = "nvidia"
    AMD = "amd"
    INTEL = "intel"
    OTHER = "other"


@dataclass
class GpuInfo:
    vendor: GpuVendor
    name: str
    vram_bytes: int = 0  # 0 = unknown / iGPU sharing system RAM
    is_discrete: bool = True  # heuristic: vendor + (>=2 GB VRAM OR Intel Arc family)

    @property
    def vram_gb(self) -> float:
        return self.vram_bytes / (1024**3)


@dataclass
class HardwareProfile:
    gpus: list[GpuInfo] = field(default_factory=list)
    cpu_brand: str = ""
    total_ram_bytes: int = 0
    probe_method: str = "unknown"  # "wmi" | "registry" | "fallback"

    @property
    def total_ram_gb(self) -> float:
        return self.total_ram_bytes / (1024**3)

    @property
    def best_gpu(self) -> GpuInfo | None:
        """Highest-ranked GPU per the discrete > iGPU > none ordering.

        Within a vendor we'd ideally use compute capability — for now we
        just take the discrete-first card. Hybrid laptops (NVIDIA dGPU +
        Intel iGPU) get the NVIDIA card here; the wizard surfaces the
        runner-up so a power-conscious user can downgrade.
        """
        if not self.gpus:
            return None
        # Rank: discrete NVIDIA > discrete AMD > Intel Arc discrete > iGPU > other
        def rank(g: GpuInfo) -> int:
            if g.vendor == GpuVendor.NVIDIA and g.is_discrete:
                return 0
            if g.vendor == GpuVendor.AMD and g.is_discrete:
                return 1
            if g.vendor == GpuVendor.INTEL and g.is_discrete:
                return 2
            if g.is_discrete:
                return 3
            return 4  # iGPU
        return sorted(self.gpus, key=rank)[0]


# ----------------------------------------------------------- vendor detect


_NVIDIA_RE = re.compile(r"\bnvidia\b|\bgeforce\b|\brtx\b|\bgtx\b|\bquadro\b|\btesla\b", re.IGNORECASE)
_AMD_RE = re.compile(r"\bamd\b|\bradeon\b|\bvega\b|\brdna\b|\bryzen\s+graphics\b", re.IGNORECASE)
_INTEL_RE = re.compile(r"\bintel\b|\barc\b|\biris\b|\buhd\b", re.IGNORECASE)


def _vendor_from_name(name: str) -> GpuVendor:
    if _NVIDIA_RE.search(name):
        return GpuVendor.NVIDIA
    if _AMD_RE.search(name):
        return GpuVendor.AMD
    if _INTEL_RE.search(name):
        return GpuVendor.INTEL
    return GpuVendor.OTHER


def _is_discrete(vendor: GpuVendor, name: str, vram_bytes: int) -> bool:
    """Heuristic split between discrete cards and integrated graphics.

    - NVIDIA on a Windows desktop is essentially always discrete.
    - Intel Arc A/B series is discrete; UHD/Iris/Graphics is iGPU.
    - AMD Radeon RX is discrete; Ryzen integrated graphics aren't.
    - VRAM >= 2 GB is a strong discrete signal; iGPUs share system RAM
      and report tiny dedicated allocations.
    """
    if vendor == GpuVendor.NVIDIA:
        return True
    if vendor == GpuVendor.INTEL:
        return "arc" in name.lower() and not re.search(r"\bgraphics\b", name, re.IGNORECASE)
    if vendor == GpuVendor.AMD:
        if re.search(r"\bradeon\s+rx\b", name, re.IGNORECASE):
            return True
        if re.search(r"\bryzen|graphics\b", name, re.IGNORECASE):
            return False
    return vram_bytes >= (2 * 1024**3)


# ----------------------------------------------------------- WMI primary

_PS_SCRIPT = (
    "$ErrorActionPreference='Stop';"
    "$gpus = Get-CimInstance Win32_VideoController | "
    "Select-Object Name, AdapterRAM;"
    "$mem  = (Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory;"
    "$cpu  = (Get-CimInstance Win32_Processor | Select-Object -First 1).Name;"
    "@{ gpus = @($gpus); ram = $mem; cpu = $cpu } | "
    "ConvertTo-Json -Compress -Depth 4"
)


def _probe_via_wmi(timeout_s: float = 3.0) -> HardwareProfile | None:
    """Run a short PowerShell script with a hard timeout."""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_SCRIPT],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        log.warning("WMI probe timed out after %.1fs — falling back to registry", timeout_s)
        return None
    except (FileNotFoundError, OSError) as e:
        log.warning("PowerShell unavailable for WMI probe (%s) — falling back to registry", e)
        return None

    if proc.returncode != 0 or not proc.stdout.strip():
        log.warning("WMI probe non-zero exit %d; stderr=%r", proc.returncode, proc.stderr[:200])
        return None

    try:
        data: Any = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        log.warning("WMI probe JSON parse failed: %s", e)
        return None

    profile = HardwareProfile(probe_method="wmi")
    profile.cpu_brand = str(data.get("cpu") or "").strip()
    profile.total_ram_bytes = int(data.get("ram") or 0)

    gpus_raw = data.get("gpus") or []
    # PowerShell ConvertTo-Json collapses a single-element array into
    # the object itself — normalize.
    if isinstance(gpus_raw, dict):
        gpus_raw = [gpus_raw]

    for g in gpus_raw:
        name = str(g.get("Name") or "").strip()
        if not name:
            continue
        # AdapterRAM is a uint32 and wraps at 4 GiB — so an RTX 5070 Ti
        # (16 GB) reports 4 GB and a 24 GB 4090 reports 4 GB. We treat
        # negative / 0 as "unknown" AND, for NVIDIA cards specifically,
        # cross-check via nvidia-smi which reports the true total. Any
        # card whose WMI VRAM is suspiciously exactly 4 GiB and is
        # NVIDIA gets the nvidia-smi value.
        vram = int(g.get("AdapterRAM") or 0)
        if vram < 0:
            vram = 0
        vendor = _vendor_from_name(name)
        # WMI AdapterRAM is uint32, capped just under 4 GiB. Anything
        # NVIDIA at or under that cap is unreliable — every current
        # consumer NVIDIA card has at least 4 GB VRAM, so a reported
        # 3.999 GiB is the uint32 ceiling, not the real size.
        if vendor == GpuVendor.NVIDIA and vram <= (4 * 1024**3):
            nvsmi_vram = _query_nvidia_vram_bytes()
            if nvsmi_vram > 0:
                log.debug(
                    "WMI reported %.1f GB for %s; nvidia-smi reports %.1f GB",
                    vram / (1024**3), name, nvsmi_vram / (1024**3),
                )
                vram = nvsmi_vram
        profile.gpus.append(
            GpuInfo(
                vendor=vendor,
                name=name,
                vram_bytes=vram,
                is_discrete=_is_discrete(vendor, name, vram),
            )
        )
    return profile


def _query_nvidia_vram_bytes(timeout_s: float = 2.0) -> int:
    """Return the largest VRAM total reported by nvidia-smi (in bytes).

    Returns 0 if nvidia-smi is missing or fails. We pick the *largest*
    when multiple GPUs are present so dual-GPU hybrid laptops don't get
    the iGPU/eGPU's tiny number as the answer.
    """
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return 0
    if proc.returncode != 0:
        return 0
    best_mib = 0
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            mib = int(line)
        except ValueError:
            continue
        if mib > best_mib:
            best_mib = mib
    return best_mib * 1024 * 1024  # MiB → bytes


# ----------------------------------------------------------- registry fallback

_VIDEO_GUID = "{4d36e968-e325-11ce-bfc1-08002be10318}"


def _probe_via_registry() -> HardwareProfile | None:
    """Read the video class registry. No timeout needed — `winreg` is
    local and fast even when WMI is hung.
    """
    if sys.platform != "win32":
        return None
    try:
        import winreg  # noqa: PLC0415  (stdlib but Windows-only)
    except ImportError:
        return None

    profile = HardwareProfile(probe_method="registry")
    base_path = r"SYSTEM\CurrentControlSet\Control\Class" + "\\" + _VIDEO_GUID
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base_path) as root:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                # Subkeys are 0000, 0001, ... Each holds a video device.
                if not re.fullmatch(r"\d{4}", sub):
                    continue
                try:
                    with winreg.OpenKey(root, sub) as adapter:
                        name, _ = winreg.QueryValueEx(adapter, "DriverDesc")
                        vram = 0
                        try:
                            vram_val, _ = winreg.QueryValueEx(
                                adapter, "HardwareInformation.qwMemorySize"
                            )
                            vram = int(vram_val) if vram_val else 0
                        except OSError:
                            pass
                except OSError:
                    continue
                name = str(name).strip()
                if not name:
                    continue
                vendor = _vendor_from_name(name)
                profile.gpus.append(
                    GpuInfo(
                        vendor=vendor,
                        name=name,
                        vram_bytes=vram,
                        is_discrete=_is_discrete(vendor, name, vram),
                    )
                )
    except OSError as e:
        log.warning("registry probe failed: %s", e)
        return None

    # Registry probe doesn't surface CPU / RAM. Leave those zeroed —
    # recommendation logic treats 0 RAM as "unknown, be conservative".
    if not profile.gpus:
        return None
    return profile


# ----------------------------------------------------------- entry point


def probe(timeout_s: float = 3.0) -> HardwareProfile:
    """Snapshot the machine. Always returns a profile — the worst case
    is an empty GPU list, which ``recommend()`` maps to CPU-only.
    """
    profile = _probe_via_wmi(timeout_s=timeout_s)
    if profile is not None and profile.gpus:
        return profile

    fallback = _probe_via_registry()
    if fallback is not None:
        # Re-use CPU/RAM from WMI partial if we had it, otherwise leave
        # the registry-only profile.
        if profile is not None:
            fallback.cpu_brand = profile.cpu_brand
            fallback.total_ram_bytes = profile.total_ram_bytes
        return fallback

    if profile is not None:
        return profile  # WMI worked but reported no GPUs — CPU-only

    # Both probes failed. Last resort: empty profile, CPU recommendation.
    log.warning("hardware probe found nothing; defaulting to CPU-only")
    return HardwareProfile(probe_method="fallback")
