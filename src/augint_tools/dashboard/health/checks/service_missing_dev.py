"""Health check: structurally a service but the ``dev`` branch is missing."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .._models import HealthCheckResult, Severity
from .._registry import register

if TYPE_CHECKING:
    from github.Repository import Repository

    from ..._data import RepoStatus
    from .. import FetchContext


class ServiceMissingDevBranchCheck:
    name = "service_missing_dev_branch"
    description = "Detect service-shaped repos whose dev branch has gone missing"

    def evaluate(
        self,
        repo: Repository,  # noqa: ARG002
        status: RepoStatus,
        *,
        config: dict,  # noqa: ARG002
        context: FetchContext,  # noqa: ARG002
    ) -> HealthCheckResult:
        # Org repos and workspace repos legitimately don't run a dev/main
        # split; ``looks_like_service`` already excludes them at the marker
        # level, but guard here too so a future signal change can't accidentally
        # start firing CRITICAL on them.
        if status.is_org or status.is_workspace:
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.OK,
                summary="not a service repo",
            )
        if status.looks_like_service and not status.has_dev_branch:
            markers = ", ".join(status.service_markers) or "service markers detected"
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.CRITICAL,
                summary=f"service repo missing dev branch ({markers})",
                link=f"https://github.com/{status.full_name}/branches",
            )
        return HealthCheckResult(
            check_name=self.name,
            severity=Severity.OK,
            summary="dev branch present",
        )


register(ServiceMissingDevBranchCheck())
