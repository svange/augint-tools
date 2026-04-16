"""System telemetry probes for the top drawer: GPU and host RAM.

Both probes are best-effort and return ``None`` on any failure: missing
tool, unsupported platform, unparseable output. The dashboard renders
only the probes that returned data, so a macOS/Windows host simply
shows nothing rather than erroring.

GPU uses ``nvidia-smi`` (any single NVIDIA card, first one wins). RAM
reads ``/proc/meminfo`` directly so we don't pull in psutil.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

_NVIDIA_SMI = shutil.which("nvidia-smi")
_MEMINFO = Path("/proc/meminfo")

_GPU_QUERY = "name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,power.limit"


@dataclass(frozen=True)
class GpuStats:
    """Snapshot of the first detected NVIDIA GPU."""

    name: str
    util_pct: int
    vram_used_mib: int
    vram_total_mib: int
    temp_c: int | None
    power_w: float | None
    power_limit_w: float | None

    @property
    def vram_used_gb(self) -> float:
        return self.vram_used_mib / 1024.0

    @property
    def vram_total_gb(self) -> float:
        return self.vram_total_mib / 1024.0

    @property
    def vram_fraction(self) -> float:
        if self.vram_total_mib <= 0:
            return 0.0
        return self.vram_used_mib / self.vram_total_mib

    @property
    def util_fraction(self) -> float:
        return max(0.0, min(1.0, self.util_pct / 100.0))


@dataclass(frozen=True)
class RamStats:
    """Snapshot of host RAM from /proc/meminfo."""

    total_kib: int
    available_kib: int

    @property
    def used_kib(self) -> int:
        return max(0, self.total_kib - self.available_kib)

    @property
    def used_gb(self) -> float:
        return self.used_kib / (1024.0 * 1024.0)

    @property
    def total_gb(self) -> float:
        return self.total_kib / (1024.0 * 1024.0)

    @property
    def available_gb(self) -> float:
        return self.available_kib / (1024.0 * 1024.0)

    @property
    def used_fraction(self) -> float:
        if self.total_kib <= 0:
            return 0.0
        return self.used_kib / self.total_kib


def probe_gpu() -> GpuStats | None:
    """Return stats for the first NVIDIA GPU, or ``None`` if unavailable."""
    if _NVIDIA_SMI is None:
        return None
    try:
        result = subprocess.run(
            [
                _NVIDIA_SMI,
                f"--query-gpu={_GPU_QUERY}",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return None
    parts = [p.strip() for p in lines[0].split(",")]
    if len(parts) < 7:
        return None
    try:
        return GpuStats(
            name=_short_gpu_name(parts[0]),
            util_pct=int(parts[1]),
            vram_used_mib=int(parts[2]),
            vram_total_mib=int(parts[3]),
            temp_c=_maybe_int(parts[4]),
            power_w=_maybe_float(parts[5]),
            power_limit_w=_maybe_float(parts[6]),
        )
    except ValueError:
        return None


def probe_ram() -> RamStats | None:
    """Return host RAM stats, or ``None`` if /proc/meminfo is unavailable."""
    try:
        raw = _MEMINFO.read_text()
    except OSError:
        return None
    total = _scan_meminfo(raw, "MemTotal:")
    available = _scan_meminfo(raw, "MemAvailable:")
    if total is None or available is None:
        return None
    return RamStats(total_kib=total, available_kib=available)


def _short_gpu_name(name: str) -> str:
    """Drop the ``NVIDIA GeForce`` prefix so the drawer stays compact."""
    for prefix in ("NVIDIA GeForce ", "NVIDIA "):
        if name.startswith(prefix):
            return name[len(prefix) :]
    return name


def _maybe_int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def _maybe_float(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


def _scan_meminfo(raw: str, key: str) -> int | None:
    """Return the kB value for ``key`` from /proc/meminfo, or ``None``."""
    for line in raw.splitlines():
        if line.startswith(key):
            parts = line.split()
            if len(parts) >= 2:
                try:
                    return int(parts[1])
                except ValueError:
                    return None
    return None
