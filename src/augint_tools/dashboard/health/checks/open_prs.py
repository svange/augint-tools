"""Health check: any open non-draft PR (including bot PRs)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .._models import HealthCheckResult, Severity
from .._registry import register

if TYPE_CHECKING:
    from github.Repository import Repository

    from ..._data import RepoStatus
    from .. import FetchContext


class OpenPRsCheck:
    name = "open_prs"
    description = "Flag repos with any open non-draft PR"

    def evaluate(
        self,
        repo: Repository,  # noqa: ARG002
        status: RepoStatus,
        *,
        config: dict,
        context: FetchContext,
    ) -> HealthCheckResult:
        threshold = config.get("open_prs_threshold", 1)

        non_draft = max(0, status.open_prs - status.draft_prs)

        if non_draft >= threshold:
            link = f"https://github.com/{status.full_name}/pulls"
            non_draft_prs = [p for p in context.pulls if not p.is_draft]
            if non_draft_prs:
                oldest = min(non_draft_prs, key=lambda p: p.created_at)
                link = oldest.url or link
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.MEDIUM,
                summary=f"({non_draft}) open PR(s)",
                link=link,
            )

        return HealthCheckResult(
            check_name=self.name,
            severity=Severity.OK,
            summary="No open PRs",
        )


register(OpenPRsCheck())
