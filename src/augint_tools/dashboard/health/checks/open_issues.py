"""Health check: high open issue count (excluding bot-created issues)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .._models import HealthCheckResult, Severity
from .._registry import register

if TYPE_CHECKING:
    from github.Repository import Repository

    from ..._data import RepoStatus

_BOT_LOGINS = {"renovate[bot]", "renovate-bot", "dependabot[bot]", "github-actions[bot]"}


class OpenIssuesCheck:
    name = "open_issues"
    description = "Flag repos with many open human-filed issues"

    def evaluate(
        self,
        repo: Repository,
        status: RepoStatus,
        *,
        config: dict,
        pulls: list | None = None,
    ) -> HealthCheckResult:
        threshold = config.get("open_issues_threshold", 1)

        # Fast path: if the total (including bots) is below threshold,
        # the human-only count can't exceed it either.
        if status.open_issues < threshold:
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.OK,
                summary=f"({status.open_issues}) open issues",
            )

        # Fetch issues to filter out bot-created noise.
        first_human_issue = None
        try:
            issues = repo.get_issues(state="open")
            human_count = 0
            for issue in issues:
                if issue.pull_request is not None:
                    continue
                if issue.user and issue.user.login in _BOT_LOGINS:
                    continue
                human_count += 1
                if first_human_issue is None:
                    first_human_issue = issue
        except Exception:
            # Fall back to the unfiltered count on API error.
            human_count = status.open_issues

        if human_count >= threshold:
            link = (
                first_human_issue.html_url
                if first_human_issue is not None
                else f"https://github.com/{status.full_name}/issues"
            )
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.MEDIUM,
                summary=f"({human_count}) open issues (excl. bots)",
                link=link,
            )

        return HealthCheckResult(
            check_name=self.name,
            severity=Severity.OK,
            summary=f"({human_count}) open issues",
        )


register(OpenIssuesCheck())
