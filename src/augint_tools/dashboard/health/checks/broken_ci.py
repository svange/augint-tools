"""Health check: broken CI pipelines on main/dev branches."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .._models import HealthCheckResult, Severity
from .._registry import register

if TYPE_CHECKING:
    from github.Repository import Repository

    from ..._data import RepoStatus
    from .. import FetchContext


class BrokenCICheck:
    name = "broken_ci"
    description = "Detect main/dev pipeline failures and missing CI"

    def evaluate(
        self,
        repo: Repository,  # noqa: ARG002
        status: RepoStatus,
        *,
        config: dict,  # noqa: ARG002
        context: FetchContext,  # noqa: ARG002
    ) -> HealthCheckResult:
        actions_url = f"https://github.com/{status.full_name}/actions"

        if status.main_status == "failure":
            detail = f": {status.main_error}" if status.main_error else ""
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.CRITICAL,
                summary=f"main pipeline failing{detail}",
                link=actions_url,
            )

        if status.dev_status == "failure":
            detail = f": {status.dev_error}" if status.dev_error else ""
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.HIGH,
                summary=f"dev pipeline failing{detail}",
                link=actions_url,
            )

        # No workflows at all is a governance gap worth surfacing.
        if status.main_status == "unknown" and status.dev_status is None:
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.MEDIUM,
                summary="No CI workflows detected",
                link=actions_url,
            )

        return HealthCheckResult(
            check_name=self.name,
            severity=Severity.OK,
            summary="CI passing",
        )


register(BrokenCICheck())
