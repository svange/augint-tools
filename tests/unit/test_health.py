"""Tests for the health check system."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from github.GithubException import GithubException

from augint_tools.dashboard._data import RepoStatus
from augint_tools.dashboard.health import (
    FetchContext,
    HealthCheckResult,
    RepoHealth,
    Severity,
    all_checks,
    available_checks,
    run_all_health_checks,
    run_health_checks,
)
from augint_tools.dashboard.health._registry import get_check

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _status(
    name="myrepo",
    full_name="org/myrepo",
    is_service=False,
    main_status="success",
    main_error=None,
    dev_status=None,
    dev_error=None,
    open_issues=0,
    open_prs=0,
    draft_prs=0,
):
    return RepoStatus(
        name=name,
        full_name=full_name,
        is_service=is_service,
        main_status=main_status,
        main_error=main_error,
        dev_status=dev_status,
        dev_error=dev_error,
        open_issues=open_issues,
        open_prs=open_prs,
        draft_prs=draft_prs,
    )


def _mock_repo():
    repo = MagicMock()
    repo.get_pulls.return_value = MagicMock(__iter__=lambda s: iter([]))
    return repo


def _mock_pr(login="user", created_at=None, html_url="https://github.com/org/repo/pull/1"):
    pr = MagicMock()
    pr.user = MagicMock()
    pr.user.login = login
    pr.created_at = created_at or datetime.now(UTC)
    pr.html_url = html_url
    pr.draft = False
    return pr


# ---------------------------------------------------------------------------
# Severity and HealthCheckResult
# ---------------------------------------------------------------------------


class TestSeverity:
    def test_ordering(self):
        assert Severity.CRITICAL < Severity.HIGH < Severity.MEDIUM < Severity.LOW < Severity.OK

    def test_min_gives_worst(self):
        assert min(Severity.OK, Severity.CRITICAL) == Severity.CRITICAL


class TestHealthCheckResult:
    def test_round_trip(self):
        result = HealthCheckResult(
            check_name="test",
            severity=Severity.HIGH,
            summary="something wrong",
            link="https://example.com",
        )
        data = result.to_dict()
        restored = HealthCheckResult.from_dict(data)
        assert restored == result

    def test_round_trip_no_link(self):
        result = HealthCheckResult(
            check_name="test",
            severity=Severity.OK,
            summary="all good",
        )
        data = result.to_dict()
        restored = HealthCheckResult.from_dict(data)
        assert restored == result
        assert restored.link is None


# ---------------------------------------------------------------------------
# RepoHealth
# ---------------------------------------------------------------------------


class TestRepoHealth:
    def test_empty_checks(self):
        health = RepoHealth(status=_status())
        assert health.worst_severity == Severity.OK
        assert health.score == Severity.OK * 1000
        assert health.findings == []

    def test_single_critical(self):
        health = RepoHealth(
            status=_status(),
            checks=[
                HealthCheckResult("ci", Severity.CRITICAL, "broken"),
                HealthCheckResult("issues", Severity.OK, "fine"),
            ],
        )
        assert health.worst_severity == Severity.CRITICAL
        assert health.findings == [HealthCheckResult("ci", Severity.CRITICAL, "broken")]

    def test_score_ordering(self):
        critical = RepoHealth(
            status=_status(name="bad"),
            checks=[HealthCheckResult("ci", Severity.CRITICAL, "broken")],
        )
        high = RepoHealth(
            status=_status(name="meh"),
            checks=[HealthCheckResult("renovate", Severity.HIGH, "missing")],
        )
        ok = RepoHealth(
            status=_status(name="good"),
            checks=[HealthCheckResult("ci", Severity.OK, "fine")],
        )
        sorted_health = sorted([ok, critical, high], key=lambda h: h.score)
        assert [h.status.name for h in sorted_health] == ["bad", "meh", "good"]

    def test_more_findings_sorts_worse(self):
        one_critical = RepoHealth(
            status=_status(name="one"),
            checks=[
                HealthCheckResult("ci", Severity.CRITICAL, "broken"),
                HealthCheckResult("other", Severity.OK, "fine"),
            ],
        )
        two_critical = RepoHealth(
            status=_status(name="two"),
            checks=[
                HealthCheckResult("ci", Severity.CRITICAL, "broken"),
                HealthCheckResult("renovate", Severity.HIGH, "missing"),
            ],
        )
        sorted_health = sorted([one_critical, two_critical], key=lambda h: h.score)
        assert sorted_health[0].status.name == "two"

    def test_round_trip(self):
        status = _status()
        health = RepoHealth(
            status=status,
            checks=[
                HealthCheckResult("ci", Severity.CRITICAL, "broken", "https://example.com"),
                HealthCheckResult("issues", Severity.OK, "fine"),
            ],
        )
        data = health.to_dict()
        restored = RepoHealth.from_dict(status, data)
        assert len(restored.checks) == 2
        assert restored.checks[0].severity == Severity.CRITICAL
        assert restored.checks[0].link == "https://example.com"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_available_checks(self):
        names = available_checks()
        assert "broken_ci" in names
        assert "renovate_enabled" in names
        assert "renovate_prs_piling" in names
        assert "security_alerts" in names
        assert "stale_prs" in names
        assert "open_issues" in names

    def test_all_checks_returns_instances(self):
        checks = all_checks()
        assert len(checks) >= 6
        for check in checks:
            assert hasattr(check, "name")
            assert hasattr(check, "evaluate")

    def test_get_check_by_name(self):
        check = get_check("broken_ci")
        assert check.name == "broken_ci"


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


class TestBrokenCI:
    def test_passing(self):
        result = get_check("broken_ci").evaluate(
            _mock_repo(), _status(main_status="success"), config={}
        )
        assert result.severity == Severity.OK

    def test_main_failing(self):
        result = get_check("broken_ci").evaluate(
            _mock_repo(),
            _status(main_status="failure", main_error="build: Run tests"),
            config={},
        )
        assert result.severity == Severity.CRITICAL
        assert "main pipeline failing" in result.summary
        assert "build: Run tests" in result.summary
        assert result.link is not None

    def test_dev_failing(self):
        result = get_check("broken_ci").evaluate(
            _mock_repo(),
            _status(
                is_service=True,
                main_status="success",
                dev_status="failure",
                dev_error="deploy: Push image",
            ),
            config={},
        )
        assert result.severity == Severity.HIGH
        assert "dev pipeline failing" in result.summary

    def test_no_ci_detected(self):
        result = get_check("broken_ci").evaluate(
            _mock_repo(),
            _status(main_status="unknown", dev_status=None),
            config={},
        )
        assert result.severity == Severity.MEDIUM
        assert "No CI workflows" in result.summary

    def test_main_takes_priority_over_dev(self):
        result = get_check("broken_ci").evaluate(
            _mock_repo(),
            _status(
                is_service=True,
                main_status="failure",
                main_error="err",
                dev_status="failure",
                dev_error="err",
            ),
            config={},
        )
        assert "main" in result.summary


class TestRenovateEnabled:
    def test_config_found(self):
        repo = _mock_repo()
        content = MagicMock()
        content.decoded_content = b'{"extends": ["config:base"]}'
        repo.get_contents.return_value = content

        result = get_check("renovate_enabled").evaluate(repo, _status(), config={})
        assert result.severity == Severity.OK
        assert "configured" in result.summary

    def test_no_config(self):
        repo = _mock_repo()
        repo.get_contents.side_effect = GithubException(404, "not found", None)

        result = get_check("renovate_enabled").evaluate(repo, _status(), config={})
        assert result.severity == Severity.HIGH
        assert "No Renovate config" in result.summary
        assert result.link is not None

    def test_empty_config_treated_as_missing(self):
        repo = _mock_repo()
        content = MagicMock()
        content.decoded_content = b"{}"
        repo.get_contents.return_value = content

        result = get_check("renovate_enabled").evaluate(repo, _status(), config={})
        assert result.severity == Severity.HIGH


class TestRenovatePRsPiling:
    def test_no_renovate_prs(self):
        result = get_check("renovate_prs_piling").evaluate(
            _mock_repo(), _status(), config={}, pulls=[]
        )
        assert result.severity == Severity.OK

    def test_below_threshold(self):
        pulls = [_mock_pr(login="renovate[bot]") for _ in range(2)]
        result = get_check("renovate_prs_piling").evaluate(
            _mock_repo(), _status(), config={}, pulls=pulls
        )
        assert result.severity == Severity.OK

    def test_above_threshold(self):
        pulls = [
            _mock_pr(
                login="renovate[bot]",
                created_at=datetime.now(UTC) - timedelta(days=i),
                html_url=f"https://github.com/org/repo/pull/{i}",
            )
            for i in range(5)
        ]
        result = get_check("renovate_prs_piling").evaluate(
            _mock_repo(), _status(), config={}, pulls=pulls
        )
        assert result.severity == Severity.HIGH
        assert "5 Renovate PRs" in result.summary
        assert result.link is not None

    def test_custom_threshold(self):
        pulls = [_mock_pr(login="renovate[bot]") for _ in range(2)]
        result = get_check("renovate_prs_piling").evaluate(
            _mock_repo(), _status(), config={"renovate_pr_threshold": 2}, pulls=pulls
        )
        assert result.severity == Severity.HIGH

    def test_mixed_authors(self):
        pulls = [
            _mock_pr(login="renovate[bot]"),
            _mock_pr(login="human-user"),
            _mock_pr(login="renovate[bot]"),
            _mock_pr(login="renovate[bot]"),
        ]
        result = get_check("renovate_prs_piling").evaluate(
            _mock_repo(), _status(), config={}, pulls=pulls
        )
        assert result.severity == Severity.HIGH
        assert "3 Renovate PRs" in result.summary


class TestSecurityAlerts:
    def test_no_alerts(self):
        repo = _mock_repo()
        repo.get_dependabot_alerts.return_value = []
        result = get_check("security_alerts").evaluate(repo, _status(), config={})
        assert result.severity == Severity.OK

    def test_critical_alerts(self):
        repo = _mock_repo()
        repo.get_dependabot_alerts.side_effect = lambda **kw: (
            [MagicMock()] if kw.get("severity") == "critical" else []
        )
        result = get_check("security_alerts").evaluate(repo, _status(), config={})
        assert result.severity == Severity.CRITICAL
        assert "1 critical" in result.summary

    def test_high_alerts_only(self):
        repo = _mock_repo()
        repo.get_dependabot_alerts.side_effect = lambda **kw: (
            [MagicMock(), MagicMock()] if kw.get("severity") == "high" else []
        )
        result = get_check("security_alerts").evaluate(repo, _status(), config={})
        assert result.severity == Severity.HIGH
        assert "2 high" in result.summary

    def test_api_unavailable(self):
        repo = _mock_repo()
        repo.get_dependabot_alerts.side_effect = GithubException(403, "forbidden", None)
        result = get_check("security_alerts").evaluate(repo, _status(), config={})
        assert result.severity == Severity.OK
        assert "unavailable" in result.summary


class TestStalePRs:
    def test_no_stale(self):
        pulls = [_mock_pr(created_at=datetime.now(UTC))]
        result = get_check("stale_prs").evaluate(_mock_repo(), _status(), config={}, pulls=pulls)
        assert result.severity == Severity.OK

    def test_stale_found(self):
        pulls = [
            _mock_pr(
                created_at=datetime.now(UTC) - timedelta(days=10),
                html_url="https://github.com/org/repo/pull/1",
            ),
            _mock_pr(created_at=datetime.now(UTC)),
        ]
        result = get_check("stale_prs").evaluate(_mock_repo(), _status(), config={}, pulls=pulls)
        assert result.severity == Severity.MEDIUM
        assert "1 stale PR" in result.summary
        assert "10d" in result.summary
        assert result.link == "https://github.com/org/repo/pull/1"

    def test_custom_threshold(self):
        pulls = [_mock_pr(created_at=datetime.now(UTC) - timedelta(days=3))]
        result = get_check("stale_prs").evaluate(
            _mock_repo(), _status(), config={"stale_pr_days": 2}, pulls=pulls
        )
        assert result.severity == Severity.MEDIUM

    def test_empty_pulls(self):
        result = get_check("stale_prs").evaluate(_mock_repo(), _status(), config={}, pulls=[])
        assert result.severity == Severity.OK

    def test_renovate_prs_excluded(self):
        pulls = [
            _mock_pr(
                login="renovate[bot]",
                created_at=datetime.now(UTC) - timedelta(days=30),
            ),
            _mock_pr(
                login="human-user",
                created_at=datetime.now(UTC),
            ),
        ]
        result = get_check("stale_prs").evaluate(_mock_repo(), _status(), config={}, pulls=pulls)
        assert result.severity == Severity.OK

    def test_default_threshold_is_7_days(self):
        pulls = [
            _mock_pr(
                created_at=datetime.now(UTC) - timedelta(days=6),
                html_url="https://github.com/org/repo/pull/1",
            ),
        ]
        result = get_check("stale_prs").evaluate(_mock_repo(), _status(), config={}, pulls=pulls)
        assert result.severity == Severity.OK


def _mock_issue(login="human-user", is_pr=False):
    issue = MagicMock()
    issue.user = MagicMock()
    issue.user.login = login
    issue.pull_request = MagicMock() if is_pr else None
    return issue


class TestOpenIssues:
    def test_below_threshold(self):
        result = get_check("open_issues").evaluate(_mock_repo(), _status(open_issues=5), config={})
        assert result.severity == Severity.OK

    def test_above_threshold_all_human(self):
        repo = _mock_repo()
        repo.get_issues.return_value = [_mock_issue() for _ in range(15)]
        result = get_check("open_issues").evaluate(repo, _status(open_issues=15), config={})
        assert result.severity == Severity.LOW
        assert "15 open issues" in result.summary
        assert result.link is not None

    def test_bot_issues_excluded(self):
        repo = _mock_repo()
        issues = [_mock_issue() for _ in range(5)] + [
            _mock_issue(login="renovate[bot]"),
            _mock_issue(login="dependabot[bot]"),
            _mock_issue(login="github-actions[bot]"),
        ]
        # Total is 8 but only 5 are human-filed.
        # Even though open_issues=12 triggers the API fetch,
        # the filtered count (5) is below the default threshold (10).
        repo.get_issues.return_value = issues
        result = get_check("open_issues").evaluate(repo, _status(open_issues=12), config={})
        assert result.severity == Severity.OK
        assert "5 open issues" in result.summary

    def test_custom_threshold(self):
        repo = _mock_repo()
        repo.get_issues.return_value = [_mock_issue() for _ in range(3)]
        result = get_check("open_issues").evaluate(
            repo, _status(open_issues=3), config={"open_issues_threshold": 3}
        )
        assert result.severity == Severity.LOW


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class TestRunHealthChecks:
    def test_runs_all_checks(self):
        repo = _mock_repo()
        content = MagicMock()
        content.decoded_content = b'{"extends": ["config:base"]}'
        repo.get_contents.return_value = content

        status = _status(main_status="success", open_issues=0)
        health = run_health_checks(repo, status, config={})

        assert len(health.checks) >= 6
        check_names = {c.check_name for c in health.checks}
        assert "broken_ci" in check_names
        assert "renovate_enabled" in check_names

    def test_check_error_does_not_crash(self):
        repo = _mock_repo()
        repo.get_contents.side_effect = RuntimeError("unexpected")

        status = _status()
        health = run_health_checks(repo, status, config={})
        assert len(health.checks) >= 6

    def test_shared_pulls_context(self):
        repo = _mock_repo()
        content = MagicMock()
        content.decoded_content = b'{"extends": ["config:base"]}'
        repo.get_contents.return_value = content

        ctx = FetchContext(pulls=[])
        health = run_health_checks(repo, _status(), config={}, context=ctx)
        # repo.get_pulls should not have been called since we provided context
        repo.get_pulls.assert_not_called()
        assert len(health.checks) >= 6


class TestRunAllHealthChecks:
    def test_sorts_worst_first(self):
        repo_bad = _mock_repo()
        repo_bad.get_contents.side_effect = GithubException(404, "not found", None)

        repo_good = _mock_repo()
        content = MagicMock()
        content.decoded_content = b'{"extends": ["config:base"]}'
        repo_good.get_contents.return_value = content

        status_bad = _status(name="bad", full_name="org/bad", main_status="failure")
        status_good = _status(name="good", full_name="org/good", main_status="success")

        healths = run_all_health_checks(
            [repo_good, repo_bad],
            [status_good, status_bad],
            config={},
        )

        assert healths[0].status.name == "bad"
        assert healths[-1].status.name == "good"
