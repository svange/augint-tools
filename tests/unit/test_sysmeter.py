"""Tests for dashboard sysmeter probes (GPU via nvidia-smi, RAM via /proc/meminfo)."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from augint_tools.dashboard import sysmeter
from augint_tools.dashboard.sysmeter import (
    GpuStats,
    RamStats,
    _scan_meminfo,
    _short_gpu_name,
    probe_gpu,
    probe_ram,
)

# ---------------------------------------------------------------------------
# probe_gpu
# ---------------------------------------------------------------------------


def _fake_completed(stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["nvidia-smi"], returncode=0, stdout=stdout, stderr="")


def test_probe_gpu_returns_none_when_nvidia_smi_missing() -> None:
    with patch.object(sysmeter, "_NVIDIA_SMI", None):
        assert probe_gpu() is None


def test_probe_gpu_parses_csv_row() -> None:
    stdout = "NVIDIA GeForce RTX 4090, 35, 8192, 24564, 62, 230.5, 450.0\n"
    with patch.object(sysmeter, "_NVIDIA_SMI", "/usr/bin/nvidia-smi"):
        with patch.object(sysmeter.subprocess, "run", return_value=_fake_completed(stdout)):
            stats = probe_gpu()
    assert stats == GpuStats(
        name="RTX 4090",
        util_pct=35,
        vram_used_mib=8192,
        vram_total_mib=24564,
        temp_c=62,
        power_w=230.5,
        power_limit_w=450.0,
    )
    assert stats is not None
    assert round(stats.vram_fraction, 3) == round(8192 / 24564, 3)
    assert round(stats.util_fraction, 3) == 0.35


def test_probe_gpu_handles_na_fields() -> None:
    stdout = "Tesla T4, 10, 512, 15109, [N/A], [N/A], [N/A]\n"
    with patch.object(sysmeter, "_NVIDIA_SMI", "/usr/bin/nvidia-smi"):
        with patch.object(sysmeter.subprocess, "run", return_value=_fake_completed(stdout)):
            stats = probe_gpu()
    assert stats is not None
    assert stats.temp_c is None
    assert stats.power_w is None
    assert stats.power_limit_w is None


def test_probe_gpu_returns_none_on_subprocess_error() -> None:
    with patch.object(sysmeter, "_NVIDIA_SMI", "/usr/bin/nvidia-smi"):
        with patch.object(
            sysmeter.subprocess,
            "run",
            side_effect=subprocess.SubprocessError("boom"),
        ):
            assert probe_gpu() is None


def test_probe_gpu_returns_none_on_short_row() -> None:
    stdout = "partial, 1, 2\n"
    with patch.object(sysmeter, "_NVIDIA_SMI", "/usr/bin/nvidia-smi"):
        with patch.object(sysmeter.subprocess, "run", return_value=_fake_completed(stdout)):
            assert probe_gpu() is None


def test_probe_gpu_returns_none_on_empty_stdout() -> None:
    with patch.object(sysmeter, "_NVIDIA_SMI", "/usr/bin/nvidia-smi"):
        with patch.object(sysmeter.subprocess, "run", return_value=_fake_completed("   \n")):
            assert probe_gpu() is None


# ---------------------------------------------------------------------------
# probe_ram
# ---------------------------------------------------------------------------


_MEMINFO_SAMPLE = """\
MemTotal:       65746604 kB
MemFree:         3244800 kB
MemAvailable:   32000000 kB
Buffers:          123456 kB
"""


def test_probe_ram_parses_meminfo(tmp_path) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(_MEMINFO_SAMPLE)
    with patch.object(sysmeter, "_MEMINFO", meminfo):
        stats = probe_ram()
    assert stats == RamStats(total_kib=65746604, available_kib=32000000)
    assert stats is not None
    assert round(stats.used_fraction, 3) == round((65746604 - 32000000) / 65746604, 3)


def test_probe_ram_returns_none_when_file_missing(tmp_path) -> None:
    missing = tmp_path / "nope"
    with patch.object(sysmeter, "_MEMINFO", missing):
        assert probe_ram() is None


def test_probe_ram_returns_none_when_keys_absent(tmp_path) -> None:
    path = tmp_path / "meminfo"
    path.write_text("Weird: 1 kB\n")
    with patch.object(sysmeter, "_MEMINFO", path):
        assert probe_ram() is None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def test_short_gpu_name_strips_prefix() -> None:
    assert _short_gpu_name("NVIDIA GeForce RTX 4090") == "RTX 4090"
    assert _short_gpu_name("NVIDIA Tesla T4") == "Tesla T4"
    assert _short_gpu_name("Quadro RTX 8000") == "Quadro RTX 8000"


def test_scan_meminfo_reads_first_match() -> None:
    assert _scan_meminfo(_MEMINFO_SAMPLE, "MemTotal:") == 65746604
    assert _scan_meminfo(_MEMINFO_SAMPLE, "Missing:") is None
