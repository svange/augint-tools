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
            # When dev is the default branch, a failing main is a promotion
            # target that hasn't received the latest pipeline yet -- not an
            # active development failure. Downgrade to LOW so it surfaces for
            # awareness without painting the card red.
            if status.default_branch == "dev" and status.dev_status not in ("failure", None):
                return HealthCheckResult(
                    check_name=self.name,
                    severity=Severity.LOW,
                    summary=f"main pipeline stale (dev is default and passing){detail}",
                    link=actions_url,
                )
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

        # "unknown" reaches here only after the GraphQL fetcher has walked
        # several commits back through the default branch looking for a
        # non-null statusCheckRollup. If nothing was found, either the repo
        # has no workflows (governance gap) or the workflows exist but haven't
        # produced a run recently enough to see. Both warrant a warning --
        # silently rendering green would claim CI health we can't actually
        # verify.
        if status.main_status == "unknown" and status.dev_status is None:
            if not status.has_workflows:
                summary = "No CI workflows detected"
            else:
                summary = "CI status unknown: no recent workflow runs"
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.MEDIUM,
                summary=summary,
                link=actions_url,
            )

        return HealthCheckResult(
            check_name=self.name,
            severity=Severity.OK,
            summary="CI passing",
        )


register(BrokenCICheck())
