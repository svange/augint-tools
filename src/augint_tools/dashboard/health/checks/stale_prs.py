"""Health check: PRs open longer than a configurable threshold."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from .._models import HealthCheckResult, Severity
from .._registry import register

if TYPE_CHECKING:
    from github.Repository import Repository

    from ..._data import RepoStatus
    from .. import FetchContext

_RENOVATE_LOGINS = {"renovate[bot]", "renovate-bot", "renovate"}


class StalePRsCheck:
    name = "stale_prs"
    description = "Detect PRs open longer than threshold (excludes Renovate PRs)"

    def evaluate(
        self,
        repo: Repository,  # noqa: ARG002
        status: RepoStatus,
        *,
        config: dict,
        context: FetchContext,
    ) -> HealthCheckResult:
        threshold_days = config.get("stale_pr_days", 7)
        now = datetime.now(UTC)

        stale = []
        for pr in context.pulls:
            # Renovate PRs are covered by renovate_prs_piling.
            if pr.author_login in _RENOVATE_LOGINS:
                continue
            age = (now - pr.created_at).days
            if age >= threshold_days:
                stale.append((pr, age))

        if stale:
            stale.sort(key=lambda x: x[1], reverse=True)
            oldest_pr, oldest_age = stale[0]
            link = oldest_pr.url or f"https://github.com/{status.full_name}/pulls"
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.MEDIUM,
                summary=f"({len(stale)}) stale PR(s), oldest {oldest_age}d",
                link=link,
            )

        return HealthCheckResult(
            check_name=self.name,
            severity=Severity.OK,
            summary="No stale PRs",
        )


register(StalePRsCheck())
