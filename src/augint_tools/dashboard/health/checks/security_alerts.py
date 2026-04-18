"""Health check: unresolved Dependabot security alerts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from github.GithubException import GithubException

from .._models import HealthCheckResult, Severity
from .._registry import register

if TYPE_CHECKING:
    from github.Repository import Repository

    from ..._data import RepoStatus


class SecurityAlertsCheck:
    name = "security_alerts"
    description = "Detect unresolved critical/high Dependabot security alerts"

    def evaluate(
        self,
        repo: Repository,
        status: RepoStatus,
        *,
        config: dict,
        pulls: list | None = None,
    ) -> HealthCheckResult:
        try:
            critical = list(repo.get_dependabot_alerts(state="open", severity="critical"))
        except GithubException:
            # Dependabot alerts may be disabled or permissions insufficient.
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.OK,
                summary="Security alerts unavailable",
            )

        if critical:
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.CRITICAL,
                summary=f"{len(critical)} critical security alert(s)",
                link=f"https://github.com/{status.full_name}/security/dependabot",
            )

        try:
            high = list(repo.get_dependabot_alerts(state="open", severity="high"))
        except GithubException:
            high = []

        if high:
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.HIGH,
                summary=f"{len(high)} high security alert(s)",
                link=f"https://github.com/{status.full_name}/security/dependabot",
            )

        return HealthCheckResult(
            check_name=self.name,
            severity=Severity.OK,
            summary="No critical/high alerts",
        )


register(SecurityAlertsCheck())
