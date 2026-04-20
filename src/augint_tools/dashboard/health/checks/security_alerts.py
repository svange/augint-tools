"""Health check: unresolved Dependabot security alerts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from github.GithubException import GithubException, UnknownObjectException
from loguru import logger

from .._models import HealthCheckResult, Severity
from .._registry import register

if TYPE_CHECKING:
    from github.Repository import Repository

    from ..._data import RepoStatus


def _is_not_enabled(exc: GithubException) -> bool:
    """Return True if ``exc`` indicates Dependabot is not enabled for the repo.

    GitHub returns 404 when a repository has not enabled Dependabot alerts
    (for example, projects using Renovate for dependency updates). Treat
    that as a non-finding rather than surfacing an error.
    """
    if isinstance(exc, UnknownObjectException):
        return True
    return getattr(exc, "status", None) == 404


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
        except GithubException as exc:
            if _is_not_enabled(exc):
                # Dependabot is not enabled on this repo (e.g. uv/Renovate
                # projects). Quietly skip -- no finding, no noisy log.
                logger.debug(
                    "Dependabot alerts not enabled for {}; skipping security check.",
                    status.full_name,
                )
                return HealthCheckResult(
                    check_name=self.name,
                    severity=Severity.OK,
                    summary="Dependabot not enabled",
                )
            # Permissions insufficient or other API failure.
            logger.debug(
                "Dependabot alerts unavailable for {}: {}",
                status.full_name,
                exc,
            )
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
        except GithubException as exc:
            logger.debug(
                "High-severity Dependabot query failed for {}: {}",
                status.full_name,
                exc,
            )
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
