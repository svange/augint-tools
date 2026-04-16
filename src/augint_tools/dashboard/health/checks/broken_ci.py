"""Health check: broken CI pipelines on main/dev branches."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .._models import HealthCheckResult, Severity
from .._registry import register

if TYPE_CHECKING:
    from github.Repository import Repository

    from ..._data import RepoStatus


class BrokenCICheck:
    name = "broken_ci"
    description = "Detect main/dev pipeline failures"

    def evaluate(
        self,
        repo: Repository,
        status: RepoStatus,
        *,
        config: dict,
        pulls: list | None = None,
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
                severity=Severity.CRITICAL,
                summary=f"dev pipeline failing{detail}",
                link=actions_url,
            )

        return HealthCheckResult(
            check_name=self.name,
            severity=Severity.OK,
            summary="CI passing",
        )


register(BrokenCICheck())
