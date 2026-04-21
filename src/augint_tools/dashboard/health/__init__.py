"""Health check system for the repo dashboard.

Public API:
    run_health_checks(repo, status, config, context) -> RepoHealth
    run_all_health_checks(repos, statuses, config) -> list[RepoHealth]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ._models import HealthCheckResult, RepoHealth, Severity
from ._registry import all_checks, available_checks, get_check, register

if TYPE_CHECKING:
    from github.Repository import Repository

    from .._data import RepoStatus
    from .._gql import IssueSnapshot, PRSnapshot

__all__ = [
    "FetchContext",
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
    """Pre-fetched data shared across health checks.

    Populated from the batched GraphQL workspace snapshot (see
    ``dashboard._gql.fetch_workspace_snapshot``). Every field is optional so
    tests and callers that only care about a subset can construct a partial
    context. Health checks must never make their own per-repo REST call --
    if a field isn't available here, the check returns an unverified result
    rather than reaching out.
    """

    pulls: list[PRSnapshot] = field(default_factory=list)
    issues: list[IssueSnapshot] = field(default_factory=list)
    # Renovate config -- first canonical path that exists, plus its text.
    renovate_config_path: str | None = None
    renovate_config_text: str | None = None
    # Pipeline workflow -- first canonical path that exists, plus its text.
    pipeline_path: str | None = None
    pipeline_text: str | None = None


def run_health_checks(
    repo: Repository,
    status: RepoStatus,
    *,
    config: dict | None = None,
    context: FetchContext | None = None,
) -> RepoHealth:
    """Run all registered checks against one repo."""
    config = config or {}
    context = context or FetchContext()

    results: list[HealthCheckResult] = []
    for check in all_checks():
        try:
            result = check.evaluate(repo, status, config=config, context=context)
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
    """Run health checks for all repos. Returns list sorted worst-first.

    Intended for tests / one-shot CLI callers that don't have a pre-built
    GraphQL snapshot to hand. Each repo gets an empty FetchContext, so
    per-repo checks that need pulls/issues/config text will return
    OK or unverified rather than doing fresh REST calls.
    """
    healths = []
    for repo, status in zip(repos, statuses, strict=True):
        healths.append(run_health_checks(repo, status, config=config))
    healths.sort(key=lambda h: h.score)
    return healths
