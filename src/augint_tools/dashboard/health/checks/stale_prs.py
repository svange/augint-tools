"""Health check: PRs open longer than a configurable threshold."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from .._models import HealthCheckResult, Severity
from .._registry import register

if TYPE_CHECKING:
    from github.Repository import Repository

    from ..._data import RepoStatus

_RENOVATE_LOGINS = {"renovate[bot]", "renovate-bot"}


class StalePRsCheck:
    name = "stale_prs"
    description = "Detect PRs open longer than threshold (excludes Renovate PRs)"

    def evaluate(
        self,
        repo: Repository,
        status: RepoStatus,
        *,
        config: dict,
        pulls: list | None = None,
    ) -> HealthCheckResult:
        threshold_days = config.get("stale_pr_days", 7)
        now = datetime.now(UTC)

        if pulls is None:
            pulls = list(repo.get_pulls(state="open"))

        stale = []
        for pr in pulls:
            # Renovate PRs are covered by renovate_prs_piling check.
            if pr.user and pr.user.login in _RENOVATE_LOGINS:
                continue
            age = (now - pr.created_at).days
            if age >= threshold_days:
                stale.append((pr, age))

        if stale:
            stale.sort(key=lambda x: x[1], reverse=True)
            oldest_pr, oldest_age = stale[0]
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.MEDIUM,
                summary=f"({len(stale)}) stale PR(s), oldest {oldest_age}d",
                link=oldest_pr.html_url,
            )

        return HealthCheckResult(
            check_name=self.name,
            severity=Severity.OK,
            summary="No stale PRs",
        )


register(StalePRsCheck())
