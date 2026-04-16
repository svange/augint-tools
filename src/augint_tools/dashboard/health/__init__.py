"""Health check system for the repo dashboard.

Public API:
    run_health_checks(repo, status, config) -> RepoHealth
    run_all_health_checks(repos, statuses, config) -> list[RepoHealth]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ._models import HealthCheckResult, RepoHealth, Severity
from ._registry import all_checks, available_checks, get_check, register

if TYPE_CHECKING:
    from github.PullRequest import PullRequest
    from github.Repository import Repository

    from .._data import RepoStatus

__all__ = [
    "HealthCheckResult",
    "RepoHealth",
    "Severity",
    "all_checks",
    "available_checks",
    "get_check",
    "register",
    "run_all_health_checks",
    "run_health_checks",
]


@dataclass
class FetchContext:
    """Pre-fetched data shared across health checks to minimize API calls."""

    pulls: list[PullRequest] = field(default_factory=list)

    @classmethod
    def build(cls, repo: Repository) -> FetchContext:
        pulls = list(repo.get_pulls(state="open"))
        return cls(pulls=pulls)


def run_health_checks(
    repo: Repository,
    status: RepoStatus,
    *,
    config: dict | None = None,
    context: FetchContext | None = None,
) -> RepoHealth:
    """Run all registered checks against one repo."""
    config = config or {}
    if context is None:
        context = FetchContext.build(repo)

    results: list[HealthCheckResult] = []
    for check in all_checks():
        try:
            result = check.evaluate(repo, status, config=config, pulls=context.pulls)
            results.append(result)
        except Exception:
            results.append(
                HealthCheckResult(
                    check_name=check.name,
                    severity=Severity.OK,
                    summary=f"{check.name}: check error",
                )
            )
    return RepoHealth(status=status, checks=results)


def run_all_health_checks(
    repos: list[Repository],
    statuses: list[RepoStatus],
    *,
    config: dict | None = None,
) -> list[RepoHealth]:
    """Run health checks for all repos. Returns list sorted worst-first."""
    healths = []
    for repo, status in zip(repos, statuses, strict=True):
        healths.append(run_health_checks(repo, status, config=config))
    healths.sort(key=lambda h: h.score)
    return healths
