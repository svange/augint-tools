"""Health check: Renovate bot PRs piling up (not auto-merging)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .._models import HealthCheckResult, Severity
from .._registry import register

if TYPE_CHECKING:
    from github.Repository import Repository

    from ..._data import RepoStatus

_RENOVATE_LOGINS = {"renovate[bot]", "renovate-bot"}


class RenovatePRsPilingCheck:
    name = "renovate_prs_piling"
    description = "Detect Renovate PRs not auto-merging"

    def evaluate(
        self,
        repo: Repository,
        status: RepoStatus,
        *,
        config: dict,
        pulls: list | None = None,
    ) -> HealthCheckResult:
        threshold = config.get("renovate_pr_threshold", 2)

        if pulls is None:
            pulls = list(repo.get_pulls(state="open"))

        renovate_prs = [p for p in pulls if p.user and p.user.login in _RENOVATE_LOGINS]

        if len(renovate_prs) >= threshold:
            oldest = min(renovate_prs, key=lambda p: p.created_at)
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.HIGH,
                summary=f"({len(renovate_prs)}) Renovate PRs piling up",
                link=oldest.html_url,
            )

        return HealthCheckResult(
            check_name=self.name,
            severity=Severity.OK,
            summary="Renovate PRs flowing normally",
        )


register(RenovatePRsPilingCheck())
