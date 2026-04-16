"""Health check: repository configuration standards (STUB)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .._models import HealthCheckResult, Severity
from .._registry import register

if TYPE_CHECKING:
    from github.Repository import Repository

    from ..._data import RepoStatus


class RepoStandardsCheck:
    name = "repo_standards"
    description = "Check repository configuration against standards"

    def evaluate(
        self,
        repo: Repository,
        status: RepoStatus,
        *,
        config: dict,
        pulls: list | None = None,
    ) -> HealthCheckResult:
        return HealthCheckResult(
            check_name=self.name,
            severity=Severity.OK,
            summary="Standards check not yet implemented",
        )


register(RepoStandardsCheck())
