"""Data models for the health check system."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .._data import RepoStatus


class Severity(IntEnum):
    """Health check severity. Lower value = worse health = sorts first."""

    CRITICAL = 1
    HIGH = 2
    MEDIUM = 3
    LOW = 4
    OK = 10


@dataclass(frozen=True)
class HealthCheckResult:
    """Output of a single health check against a single repo."""

    check_name: str
    severity: Severity
    summary: str
    link: str | None = None

    def to_dict(self) -> dict:
        return {
            "check_name": self.check_name,
            "severity": self.severity.value,
            "summary": self.summary,
            "link": self.link,
        }

    @classmethod
    def from_dict(cls, data: dict) -> HealthCheckResult:
        return cls(
            check_name=data["check_name"],
            severity=Severity(data["severity"]),
            summary=data["summary"],
            link=data.get("link"),
        )


@dataclass
class RepoHealth:
    """Aggregated health assessment for a single repository.

    Wraps a RepoStatus and supplements it with health check results.
    """

    status: RepoStatus
    checks: list[HealthCheckResult] = field(default_factory=list)
    # ISO-8601 UTC timestamp of when this repo most recently transitioned from
    # a green state into a warning (yellow) state. Set by the dashboard when it
    # commits a new refresh, not by the health checks themselves. Used to drive
    # the card-border flash in the TUI while still within a short window.
    warning_since: str | None = None

    @property
    def worst_severity(self) -> Severity:
        """The most severe finding across all checks."""
        if not self.checks:
            return Severity.OK
        return min(c.severity for c in self.checks)

    @property
    def score(self) -> int:
        """Composite score for sorting. Lower = worse health.

        Primary: worst severity. Secondary: number of non-OK findings.
        """
        if not self.checks:
            return Severity.OK * 1000
        non_ok = sum(1 for c in self.checks if c.severity != Severity.OK)
        return int(self.worst_severity) * 1000 - non_ok * 10

    @property
    def passed_checks(self) -> int:
        """Number of checks that passed (severity == OK)."""
        return sum(1 for c in self.checks if c.severity == Severity.OK)

    @property
    def total_checks(self) -> int:
        """Total number of checks that ran."""
        return len(self.checks)

    @property
    def findings(self) -> list[HealthCheckResult]:
        """Non-OK check results, sorted worst-first."""
        return sorted(
            (c for c in self.checks if c.severity != Severity.OK),
            key=lambda c: c.severity,
        )

    def to_dict(self) -> dict:
        return {
            "checks": [c.to_dict() for c in self.checks],
            "warning_since": self.warning_since,
        }

    @classmethod
    def from_dict(cls, status: RepoStatus, data: dict) -> RepoHealth:
        return cls(
            status=status,
            checks=[HealthCheckResult.from_dict(c) for c in data.get("checks", [])],
            warning_since=data.get("warning_since"),
        )
