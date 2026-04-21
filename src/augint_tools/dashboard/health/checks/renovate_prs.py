"""Health check: Renovate bot PRs piling up (not auto-merging)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .._models import HealthCheckResult, Severity
from .._registry import register

if TYPE_CHECKING:
    from github.Repository import Repository

    from ..._data import RepoStatus
    from .. import FetchContext

_RENOVATE_LOGINS = {"renovate[bot]", "renovate-bot"}


class RenovatePRsPilingCheck:
    name = "renovate_prs_piling"
    description = "Detect Renovate PRs not auto-merging"

    def evaluate(
        self,
        repo: Repository,  # noqa: ARG002
        status: RepoStatus,
        *,
        config: dict,
        context: FetchContext,
    ) -> HealthCheckResult:
        threshold = config.get("renovate_pr_threshold", 2)

        renovate_prs = [p for p in context.pulls if p.author_login in _RENOVATE_LOGINS]

        if len(renovate_prs) >= threshold:
            oldest = min(renovate_prs, key=lambda p: p.created_at)
            link = oldest.url or f"https://github.com/{status.full_name}/pulls"
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.HIGH,
                summary=f"({len(renovate_prs)}) Renovate PRs piling up",
                link=link,
            )

        return HealthCheckResult(
            check_name=self.name,
            severity=Severity.OK,
            summary="Renovate PRs flowing normally",
        )


register(RenovatePRsPilingCheck())
