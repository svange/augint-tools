"""Health check: Renovate configuration presence and validity.

Reads the pre-fetched config text from ``FetchContext.renovate_config_text``
(populated by the batched GraphQL workspace query, which probes every
canonical path in the same round-trip as every other field). No per-repo
REST call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .._models import HealthCheckResult, Severity
from .._registry import register

if TYPE_CHECKING:
    from github.Repository import Repository

    from ..._data import RepoStatus
    from .. import FetchContext


class RenovateEnabledCheck:
    name = "renovate_enabled"
    description = "Check for valid Renovate configuration"

    def evaluate(
        self,
        repo: Repository,  # noqa: ARG002
        status: RepoStatus,
        *,
        config: dict,  # noqa: ARG002
        context: FetchContext,
    ) -> HealthCheckResult:
        path = context.renovate_config_path
        text = (context.renovate_config_text or "").strip()

        if path and len(text) > 2:
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.OK,
                summary=f"Renovate configured ({path})",
            )

        return HealthCheckResult(
            check_name=self.name,
            severity=Severity.HIGH,
            summary="No Renovate config found",
            link=f"https://github.com/{status.full_name}",
        )


register(RenovateEnabledCheck())
