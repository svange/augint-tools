from __future__ import annotations

import socket
from types import SimpleNamespace

from augint_tools.dashboard import sysprobe


def test_read_cpu_times_happy_path(monkeypatch):
    stat_file = SimpleNamespace(read_text=lambda: "cpu  1 2 3 4 5 6 7 8\n")
    monkeypatch.setattr(sysprobe, "_PROC_STAT", stat_file)
    assert sysprobe._read_cpu_times() == [1, 2, 3, 4, 5, 6, 7]


def test_read_cpu_times_handles_oserror(monkeypatch):
    stat_file = SimpleNamespace(read_text=lambda: (_ for _ in ()).throw(OSError("boom")))
    monkeypatch.setattr(sysprobe, "_PROC_STAT", stat_file)
    assert sysprobe._read_cpu_times() is None


def test_probe_cpu_returns_none_when_sample_unavailable(monkeypatch):
    monkeypatch.setattr(sysprobe, "_read_cpu_times", lambda: None)
    assert sysprobe.probe_cpu() is None


def test_probe_cpu_computes_usage(monkeypatch):
    samples = iter([[10, 0, 0, 10, 0, 0, 0], [20, 0, 0, 15, 0, 0, 0]])
    monkeypatch.setattr(sysprobe, "_read_cpu_times", lambda: next(samples))
    monkeypatch.setattr(sysprobe.time, "sleep", lambda _: None)
    monkeypatch.setattr(sysprobe.os, "cpu_count", lambda: 8)
    monkeypatch.setattr(sysprobe.os, "getloadavg", lambda: (1.234, 2.345, 3.456))
    cpu = sysprobe.probe_cpu()
    assert cpu is not None
    assert cpu.usage_pct == 66.7
    assert cpu.core_count == 8
    assert cpu.load_avg_1m == 1.23


def test_probe_docker_returns_empty_on_run_error(monkeypatch):
    monkeypatch.setattr(
        sysprobe.subprocess,
        "run",
        lambda *_, **__: (_ for _ in ()).throw(FileNotFoundError("no docker")),
    )
    stats = sysprobe.probe_docker()
    assert stats.docker_available is False
    assert stats.containers == ()


def test_probe_docker_parses_containers(monkeypatch):
    output = (
        '{"ID":"1","Names":"augint-shell-1","Image":"img","State":"running","CreatedAt":"now"}\n'
        '{"ID":"2","Names":"db","Image":"pg","Status":"up","RunningFor":"1h"}\n'
        "not-json\n"
    )
    monkeypatch.setattr(
        sysprobe.subprocess,
        "run",
        lambda *_, **__: SimpleNamespace(returncode=0, stdout=output),
    )
    stats = sysprobe.probe_docker()
    assert stats.docker_available is True
    assert stats.total_running == 2
    assert stats.augint_shell_count == 1
    assert len(stats.containers) == 2


class _FakeSocket:
    def __init__(self, should_fail: bool = False):
        self.should_fail = should_fail

    def settimeout(self, _: float) -> None:
        return None

    def connect(self, _: tuple[str, int]) -> None:
        if self.should_fail:
            raise OSError("connect failed")

    def close(self) -> None:
        return None


def test_tcp_ping_success_and_failure(monkeypatch):
    monkeypatch.setattr(sysprobe.socket, "socket", lambda *_: _FakeSocket(False))
    assert sysprobe._tcp_ping("example.com", 443, 1.0) is not None
    monkeypatch.setattr(sysprobe.socket, "socket", lambda *_: _FakeSocket(True))
    assert sysprobe._tcp_ping("example.com", 443, 1.0) is None


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def sendall(self, _: bytes) -> None:
        return None

    def recv(self, _: int) -> bytes:
        return b"HTTP/1.1 204 No Content\r\n"


def test_http_check_success_and_failure(monkeypatch):
    monkeypatch.setattr(sysprobe.socket, "create_connection", lambda *_args, **_kwargs: _Conn())
    assert sysprobe._http_check(1.0) is not None
    monkeypatch.setattr(
        sysprobe.socket,
        "create_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("down")),
    )
    assert sysprobe._http_check(1.0) is None


def test_probe_network_uses_tcp_or_http(monkeypatch):
    monkeypatch.setattr(sysprobe, "_tcp_ping", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(sysprobe, "_http_check", lambda *_args, **_kwargs: 12.3)
    stats = sysprobe.probe_network()
    assert stats.connected is True
    assert stats.http_reachable is True


def test_probe_ping(monkeypatch):
    monkeypatch.setattr(sysprobe, "_tcp_ping", lambda *_args, **_kwargs: 4.2)
    ping = sysprobe.probe_ping()
    assert ping.connected is True
    assert ping.latency_ms == 4.2


def test_resolve_hostname_success_and_errors(monkeypatch):
    set_calls: list[float | None] = []
    monkeypatch.setattr(sysprobe.socket, "getdefaulttimeout", lambda: 2.0)
    monkeypatch.setattr(sysprobe.socket, "setdefaulttimeout", lambda v: set_calls.append(v))
    monkeypatch.setattr(sysprobe.socket, "getaddrinfo", lambda *_: [("ok",)])
    ok = sysprobe._resolve_hostname("example.com")
    assert ok.resolved is True
    assert set_calls[0] == 5.0 and set_calls[-1] == 2.0

    monkeypatch.setattr(
        sysprobe.socket,
        "getaddrinfo",
        lambda *_: (_ for _ in ()).throw(socket.gaierror("no host")),
    )
    failed = sysprobe._resolve_hostname("missing.example")
    assert failed.resolved is False


def test_probe_dns_collects_unique_hosts(monkeypatch):
    monkeypatch.setattr(
        sysprobe,
        "_resolve_hostname",
        lambda host: sysprobe.DnsCheckResult(hostname=host, resolved=True),
    )
    results = sysprobe.probe_dns(
        {
            "org/repo-a": ["https://example.com/a", "https://api.example.com/x"],
            "org/repo-b": ["https://example.com/b"],
            "org/repo-c": ["notaurl"],
        }
    )
    by_host = {r.hostname: r for r in results}
    assert set(by_host) == {"api.example.com", "example.com"}
    assert by_host["example.com"].repos == ("org/repo-a", "org/repo-b")


def test_probe_system_handles_probe_failures(monkeypatch):
    monkeypatch.setattr(sysprobe, "probe_cpu", lambda: (_ for _ in ()).throw(RuntimeError("cpu")))
    monkeypatch.setattr(
        sysprobe, "probe_docker", lambda: (_ for _ in ()).throw(RuntimeError("docker"))
    )
    monkeypatch.setattr(
        sysprobe, "probe_network", lambda: (_ for _ in ()).throw(RuntimeError("network"))
    )
    snapshot = sysprobe.probe_system()
    assert snapshot.cpu is None
    assert snapshot.docker.docker_available is False
    assert snapshot.network.connected is False
