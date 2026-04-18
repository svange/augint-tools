"""Health check: Renovate configuration presence and validity."""

from __future__ import annotations

from typing import TYPE_CHECKING

from github.GithubException import GithubException

from .._models import HealthCheckResult, Severity
from .._registry import register

if TYPE_CHECKING:
    from github.Repository import Repository

    from ..._data import RepoStatus

_CONFIG_PATHS = (
    "renovate.json5",  # canonical path -- probe first for short-circuit
    "renovate.json",
    ".github/renovate.json5",
    ".github/renovate.json",
    ".renovaterc",
    ".renovaterc.json",
)


class RenovateEnabledCheck:
    name = "renovate_enabled"
    description = "Check for valid Renovate configuration"

    def evaluate(
        self,
        repo: Repository,
        status: RepoStatus,
        *,
        config: dict,
        pulls: list | None = None,
    ) -> HealthCheckResult:
        for path in _CONFIG_PATHS:
            try:
                content_file = repo.get_contents(path)
                if isinstance(content_file, list):
                    continue
                raw = content_file.decoded_content.decode("utf-8").strip()
                if len(raw) > 2:
                    return HealthCheckResult(
                        check_name=self.name,
                        severity=Severity.OK,
                        summary=f"Renovate configured ({path})",
                    )
            except GithubException:
                continue

        return HealthCheckResult(
            check_name=self.name,
            severity=Severity.HIGH,
            summary="No Renovate config found",
            link=f"https://github.com/{status.full_name}",
        )


register(RenovateEnabledCheck())
