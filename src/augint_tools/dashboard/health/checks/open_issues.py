"""Health check: high open issue count (excluding bot-created issues).

Uses ``RepoStatus.human_open_issues`` which is already computed during the
main GraphQL workspace fetch -- no duplicate REST call from this check.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .._models import HealthCheckResult, Severity
from .._registry import register

if TYPE_CHECKING:
    from github.Repository import Repository

    from ..._data import RepoStatus
    from .. import FetchContext


class OpenIssuesCheck:
    name = "open_issues"
    description = "Flag repos with many open human-filed issues"

    def evaluate(
        self,
        repo: Repository,  # noqa: ARG002
        status: RepoStatus,
        *,
        config: dict,
        context: FetchContext,  # noqa: ARG002
    ) -> HealthCheckResult:
        threshold = config.get("open_issues_threshold", 10)
        human_count = status.human_open_issues

        if human_count >= threshold:
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.LOW,
                summary=f"({human_count}) open issues (excl. bots)",
                link=f"https://github.com/{status.full_name}/issues",
            )

        return HealthCheckResult(
            check_name=self.name,
            severity=Severity.OK,
            summary=f"({human_count}) open issues",
        )


register(OpenIssuesCheck())
