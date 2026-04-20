"""Extended system probing: CPU, Docker containers, and network connectivity.

All probe functions are data-only (no UI), thread-safe, and handle missing
tools gracefully. Each returns a frozen dataclass. Safe to call from Textual
worker threads.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_PROC_STAT = Path("/proc/stat")

# ---------------------------------------------------------------------------
# CPU
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CpuStats:
    """Snapshot of CPU utilisation and load averages."""

    usage_pct: float
    core_count: int
    load_avg_1m: float
    load_avg_5m: float
    load_avg_15m: float


def _read_cpu_times() -> list[int] | None:
    """Read aggregate CPU time fields from /proc/stat."""
    try:
        text = _PROC_STAT.read_text()
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("cpu "):
            parts = line.split()
            if len(parts) >= 8:
                return [int(p) for p in parts[1:8]]
    return None


def probe_cpu() -> CpuStats | None:
    """Compute CPU usage over a ~0.1s sample window.

    Returns ``None`` if /proc/stat is not available (e.g. macOS).
    """
    t1 = _read_cpu_times()
    if t1 is None:
        return None

    time.sleep(0.1)

    t2 = _read_cpu_times()
    if t2 is None:
        return None

    delta = [b - a for a, b in zip(t1, t2, strict=True)]
    total = sum(delta)
    if total == 0:
        usage_pct = 0.0
    else:
        idle = delta[3]  # 4th field is idle
        usage_pct = round((1.0 - idle / total) * 100.0, 1)

    core_count = os.cpu_count() or 1

    try:
        load1, load5, load15 = os.getloadavg()
    except OSError:
        load1 = load5 = load15 = 0.0

    return CpuStats(
        usage_pct=usage_pct,
        core_count=core_count,
        load_avg_1m=round(load1, 2),
        load_avg_5m=round(load5, 2),
        load_avg_15m=round(load15, 2),
    )


# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DockerContainer:
    """Single Docker container record."""

    container_id: str
    name: str
    image: str
    status: str
    created: str
    is_augint_shell: bool


@dataclass(frozen=True)
class DockerStats:
    """Aggregate Docker state."""

    containers: tuple[DockerContainer, ...]
    total_running: int
    augint_shell_count: int
    docker_available: bool


def probe_docker() -> DockerStats:
    """Query Docker for container state.

    Returns a ``DockerStats`` with ``docker_available=False`` if the docker
    CLI is missing or the daemon is unreachable.
    """
    empty = DockerStats(
        containers=(),
        total_running=0,
        augint_shell_count=0,
        docker_available=False,
    )
    try:
        result = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{json .}}"],
            capture_output=True,
            text=True,
            timeout=3.0,
        )
    except (subprocess.SubprocessError, OSError, FileNotFoundError):
        return empty

    if result.returncode != 0:
        return empty

    containers: list[DockerContainer] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = obj.get("Names", "")
        containers.append(
            DockerContainer(
                container_id=obj.get("ID", ""),
                name=name,
                image=obj.get("Image", ""),
                status=obj.get("State", obj.get("Status", "")),
                created=obj.get("CreatedAt", obj.get("RunningFor", "")),
                is_augint_shell="augint-shell" in name,
            )
        )

    running = sum(1 for c in containers if c.status.lower() in ("running", "up"))
    augint_count = sum(1 for c in containers if c.is_augint_shell)

    return DockerStats(
        containers=tuple(containers),
        total_running=running,
        augint_shell_count=augint_count,
        docker_available=True,
    )


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NetworkStats:
    """Snapshot of network connectivity."""

    connected: bool
    latency_ms: float | None
    http_reachable: bool
    http_latency_ms: float | None
    last_check_at: str | None


def _tcp_ping(host: str, port: int, timeout: float) -> float | None:
    """Measure TCP connect RTT in milliseconds. Returns None on failure."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        start = time.perf_counter()
        sock.connect((host, port))
        elapsed = (time.perf_counter() - start) * 1000.0
        sock.close()
        return round(elapsed, 2)
    except OSError:
        return None


def _http_check(timeout: float) -> float | None:
    """HTTP connectivity check. Returns RTT in ms or None on failure."""
    url = "http://connectivitycheck.gstatic.com/generate_204"
    try:
        start = time.perf_counter()
        resp = urllib.request.urlopen(url, timeout=timeout)  # noqa: S310  # nosec B310
        resp.read()
        resp.close()
        elapsed = (time.perf_counter() - start) * 1000.0
        return round(elapsed, 2)
    except (OSError, urllib.error.URLError):
        return None


def probe_network() -> NetworkStats:
    """Check network connectivity via TCP and HTTP.

    TCP connect to 1.1.1.1:443 and HTTP GET to gstatic captive-portal
    endpoint. Either succeeding means we are connected.
    """
    now = datetime.now(tz=UTC).isoformat()
    tcp_latency = _tcp_ping("1.1.1.1", 443, timeout=3.0)
    http_latency = _http_check(timeout=3.0)

    connected = tcp_latency is not None or http_latency is not None

    return NetworkStats(
        connected=connected,
        latency_ms=tcp_latency,
        http_reachable=http_latency is not None,
        http_latency_ms=http_latency,
        last_check_at=now,
    )


# ---------------------------------------------------------------------------
# Combined snapshot
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SystemSnapshot:
    """Aggregated result of all system probes."""

    cpu: CpuStats | None
    docker: DockerStats
    network: NetworkStats
    timestamp: str


def probe_system() -> SystemSnapshot:
    """Run all system probes. Safe to call from a worker thread.

    Each probe is independently guarded so one failure does not block
    the others.
    """
    now = datetime.now(tz=UTC).isoformat()

    try:
        cpu = probe_cpu()
    except Exception:
        cpu = None

    try:
        docker = probe_docker()
    except Exception:
        docker = DockerStats(
            containers=(),
            total_running=0,
            augint_shell_count=0,
            docker_available=False,
        )

    try:
        network = probe_network()
    except Exception:
        network = NetworkStats(
            connected=False,
            latency_ms=None,
            http_reachable=False,
            http_latency_ms=None,
            last_check_at=now,
        )

    return SystemSnapshot(
        cpu=cpu,
        docker=docker,
        network=network,
        timestamp=now,
    )
