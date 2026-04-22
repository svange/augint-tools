"""Health check: open human-filed issues — count and age.

Uses ``RepoStatus.human_open_issues`` and ``RepoStatus.oldest_issue_created_at``
(both pre-computed during the main GraphQL workspace fetch -- no extra REST
calls from this check). The card always shows a numerical issue counter on the
CI line, so a "quiet" (OK-severity) result here deliberately emits no info
row. Severity only escalates when there are *too many* issues or the oldest
issue has sat too long.

Thresholds default to 7 days (stale -> MEDIUM) and 30 days (ancient ->
CRITICAL); the widget's counts-line color tiers use the same values (see
``repo_card._ISSUE_STALE_AGE`` / ``_ISSUE_CRITICAL_AGE``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from .._models import HealthCheckResult, Severity
from .._registry import register

if TYPE_CHECKING:
    from github.Repository import Repository

    from ..._data import RepoStatus
    from .. import FetchContext


def _issue_age_days(iso_ts: str | None) -> int | None:
    if not iso_ts:
        return None
    try:
        ts = datetime.fromisoformat(iso_ts)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return max(0, (datetime.now(UTC) - ts).days)


class OpenIssuesCheck:
    name = "open_issues"
    description = "Flag repos with many or long-open human-filed issues"

    def evaluate(
        self,
        repo: Repository,  # noqa: ARG002
        status: RepoStatus,
        *,
        config: dict,
        context: FetchContext,  # noqa: ARG002
    ) -> HealthCheckResult:
        count_threshold = config.get("open_issues_threshold", 10)
        stale_days = config.get("stale_issue_days", 7)
        critical_days = config.get("critical_issue_days", 30)

        human_count = status.human_open_issues
        link = f"https://github.com/{status.full_name}/issues"
        age = _issue_age_days(status.oldest_issue_created_at)

        if age is not None and age >= critical_days:
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.CRITICAL,
                summary=f"({human_count}) open issues, oldest {age}d",
                link=link,
            )

        too_many = human_count >= count_threshold
        too_old = age is not None and age >= stale_days

        if too_many and too_old:
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.MEDIUM,
                summary=f"({human_count}) open issues, oldest {age}d",
                link=link,
            )
        if too_old:
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.MEDIUM,
                summary=f"oldest issue {age}d ({human_count} open)",
                link=link,
            )
        if too_many:
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
