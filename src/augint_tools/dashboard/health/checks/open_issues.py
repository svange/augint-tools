"""Health check: high open issue count."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .._models import HealthCheckResult, Severity
from .._registry import register

if TYPE_CHECKING:
    from github.Repository import Repository

    from ..._data import RepoStatus


class OpenIssuesCheck:
    name = "open_issues"
    description = "Flag repos with many open issues"

    def evaluate(
        self,
        repo: Repository,
        status: RepoStatus,
        *,
        config: dict,
        pulls: list | None = None,
    ) -> HealthCheckResult:
        threshold = config.get("open_issues_threshold", 10)

        if status.open_issues >= threshold:
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.LOW,
                summary=f"{status.open_issues} open issues",
                link=f"https://github.com/{status.full_name}/issues",
            )

        return HealthCheckResult(
            check_name=self.name,
            severity=Severity.OK,
            summary=f"{status.open_issues} open issues",
        )


register(OpenIssuesCheck())
