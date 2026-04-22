"""Tests for the health check system."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from augint_tools.dashboard._data import RepoStatus
from augint_tools.dashboard._gql import IssueSnapshot, PRSnapshot
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
    has_dev_branch=False,
    main_status="success",
    main_error=None,
    dev_status=None,
    dev_error=None,
    open_issues=0,
    open_prs=0,
    draft_prs=0,
    human_open_issues=0,
    default_branch="main",
    has_workflows=True,
    looks_like_service=False,
    service_markers=(),
    is_org=False,
    is_workspace=False,
):
    return RepoStatus(
        name=name,
        full_name=full_name,
        has_dev_branch=has_dev_branch,
        main_status=main_status,
        main_error=main_error,
        dev_status=dev_status,
        dev_error=dev_error,
        open_issues=open_issues,
        open_prs=open_prs,
        draft_prs=draft_prs,
        human_open_issues=human_open_issues,
        default_branch=default_branch,
        has_workflows=has_workflows,
        looks_like_service=looks_like_service,
        service_markers=service_markers,
        is_org=is_org,
        is_workspace=is_workspace,
    )


def _mock_repo():
    """Mock PyGithub Repository -- checks no longer hit it, but the positional
    is still present in the Protocol signature."""
    return MagicMock()


def _pr(
    login: str = "user",
    created_at: datetime | None = None,
    url: str = "https://github.com/org/repo/pull/1",
    is_draft: bool = False,
    number: int = 1,
) -> PRSnapshot:
    return PRSnapshot(
        number=number,
        is_draft=is_draft,
        created_at=created_at or datetime.now(UTC),
        author_login=login,
        url=url,
    )


def _issue(
    login: str = "human-user",
    created_at: datetime | None = None,
    number: int = 1,
) -> IssueSnapshot:
    return IssueSnapshot(
        number=number,
        created_at=created_at or datetime.now(UTC),
        author_login=login,
    )


def _ctx(
    pulls: list[PRSnapshot] | None = None,
    issues: list[IssueSnapshot] | None = None,
    renovate_config_path: str | None = None,
    renovate_config_text: str | None = None,
    pipeline_path: str | None = None,
    pipeline_text: str | None = None,
) -> FetchContext:
    return FetchContext(
        pulls=pulls or [],
        issues=issues or [],
        renovate_config_path=renovate_config_path,
        renovate_config_text=renovate_config_text,
        pipeline_path=pipeline_path,
        pipeline_text=pipeline_text,
    )


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
        assert "stale_prs" in names
        assert "open_issues" in names
        assert "open_prs" in names
        assert "coverage" in names
        assert "service_missing_dev_branch" in names

    def test_all_checks_returns_instances(self):
        checks = all_checks()
        assert len(checks) >= 5
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
            _mock_repo(), _status(main_status="success"), config={}, context=_ctx()
        )
        assert result.severity == Severity.OK

    def test_main_failing(self):
        result = get_check("broken_ci").evaluate(
            _mock_repo(),
            _status(main_status="failure", main_error="build: Run tests"),
            config={},
            context=_ctx(),
        )
        assert result.severity == Severity.CRITICAL
        assert "main pipeline failing" in result.summary
        assert "build: Run tests" in result.summary
        assert result.link is not None

    def test_dev_failing(self):
        result = get_check("broken_ci").evaluate(
            _mock_repo(),
            _status(
                has_dev_branch=True,
                looks_like_service=True,
                main_status="success",
                dev_status="failure",
                dev_error="deploy: Push image",
            ),
            config={},
            context=_ctx(),
        )
        assert result.severity == Severity.HIGH
        assert "dev pipeline failing" in result.summary

    def test_no_ci_detected_only_when_no_workflow_files(self):
        # Truly no workflows -> flagged.
        result = get_check("broken_ci").evaluate(
            _mock_repo(),
            _status(main_status="unknown", dev_status=None, has_workflows=False),
            config={},
            context=_ctx(),
        )
        assert result.severity == Severity.MEDIUM
        assert "No CI workflows" in result.summary

    def test_unknown_rollup_with_workflows_is_warning(self):
        # History walkback in the GraphQL fetcher already absorbs the common
        # skip-ci-chore-commit case. Anything that still arrives here as
        # "unknown" means 5+ consecutive commits produced no rollup -- not
        # verifiably healthy, so don't claim green.
        result = get_check("broken_ci").evaluate(
            _mock_repo(),
            _status(main_status="unknown", dev_status=None, has_workflows=True),
            config={},
            context=_ctx(),
        )
        assert result.severity == Severity.MEDIUM
        assert "unknown" in result.summary.lower()

    def test_main_takes_priority_over_dev(self):
        result = get_check("broken_ci").evaluate(
            _mock_repo(),
            _status(
                has_dev_branch=True,
                looks_like_service=True,
                main_status="failure",
                main_error="err",
                dev_status="failure",
                dev_error="err",
            ),
            config={},
            context=_ctx(),
        )
        assert "main" in result.summary


class TestServiceMissingDevBranch:
    """A repo with structural service markers but no dev branch is the exact
    drift the dashboard exists to catch -- e.g. a SAM/CDK/Dockerfile service
    whose dev branch was deleted because the standard ruleset's deletion rule
    wasn't in place. The check fires CRITICAL because the dev-pinned workflows
    will start poisoning the main rollup, which is a noisy and indirect signal
    compared to naming the actual condition."""

    def test_service_with_dev_branch_is_ok(self):
        result = get_check("service_missing_dev_branch").evaluate(
            _mock_repo(),
            _status(
                has_dev_branch=True,
                looks_like_service=True,
                service_markers=("template.yaml",),
            ),
            config={},
            context=_ctx(),
        )
        assert result.severity == Severity.OK

    def test_service_without_dev_branch_is_critical(self):
        result = get_check("service_missing_dev_branch").evaluate(
            _mock_repo(),
            _status(
                has_dev_branch=False,
                looks_like_service=True,
                service_markers=("template.yaml", "Dockerfile"),
            ),
            config={},
            context=_ctx(),
        )
        assert result.severity == Severity.CRITICAL
        assert "service repo missing dev branch" in result.summary
        assert "template.yaml" in result.summary
        assert "Dockerfile" in result.summary
        assert result.link == "https://github.com/org/myrepo/branches"

    def test_library_without_dev_branch_is_ok(self):
        # No service markers -> not a service -> dev branch absence is expected,
        # not a problem. This is what guards the check from false positives on
        # ordinary library repos.
        result = get_check("service_missing_dev_branch").evaluate(
            _mock_repo(),
            _status(has_dev_branch=False, looks_like_service=False),
            config={},
            context=_ctx(),
        )
        assert result.severity == Severity.OK

    def test_library_with_dev_branch_is_ok(self):
        # Some library repos legitimately maintain a dev branch (long-running
        # rewrite, doc staging, etc.). Not a service, dev exists -> nothing to flag.
        result = get_check("service_missing_dev_branch").evaluate(
            _mock_repo(),
            _status(has_dev_branch=True, looks_like_service=False),
            config={},
            context=_ctx(),
        )
        assert result.severity == Severity.OK

    def test_org_repo_is_never_flagged(self):
        # ``-org`` repos hold AWS Organization IaC, never run a dev split,
        # and may publish convenience packages off main. Even if some future
        # signal change set looks_like_service=True on one, the check must
        # not fire.
        result = get_check("service_missing_dev_branch").evaluate(
            _mock_repo(),
            _status(
                is_org=True,
                has_dev_branch=False,
                looks_like_service=True,
                service_markers=("template.yaml",),
            ),
            config={},
            context=_ctx(),
        )
        assert result.severity == Severity.OK

    def test_workspace_repo_is_never_flagged(self):
        result = get_check("service_missing_dev_branch").evaluate(
            _mock_repo(),
            _status(is_workspace=True, has_dev_branch=False, looks_like_service=False),
            config={},
            context=_ctx(),
        )
        assert result.severity == Severity.OK


class TestRenovateEnabled:
    def test_config_found(self):
        ctx = _ctx(
            renovate_config_path="renovate.json5",
            renovate_config_text='{"extends": ["config:base"]}',
        )
        result = get_check("renovate_enabled").evaluate(
            _mock_repo(), _status(), config={}, context=ctx
        )
        assert result.severity == Severity.OK
        assert "configured" in result.summary

    def test_no_config(self):
        result = get_check("renovate_enabled").evaluate(
            _mock_repo(), _status(), config={}, context=_ctx()
        )
        assert result.severity == Severity.HIGH
        assert "No Renovate config" in result.summary
        assert result.link is not None

    def test_empty_config_treated_as_missing(self):
        ctx = _ctx(renovate_config_path="renovate.json", renovate_config_text="{}")
        result = get_check("renovate_enabled").evaluate(
            _mock_repo(), _status(), config={}, context=ctx
        )
        assert result.severity == Severity.HIGH


class TestRenovatePRsPiling:
    def test_no_renovate_prs(self):
        result = get_check("renovate_prs_piling").evaluate(
            _mock_repo(), _status(), config={}, context=_ctx(pulls=[])
        )
        assert result.severity == Severity.OK

    def test_below_threshold(self):
        pulls = [_pr(login="renovate[bot]")]
        result = get_check("renovate_prs_piling").evaluate(
            _mock_repo(), _status(), config={}, context=_ctx(pulls=pulls)
        )
        assert result.severity == Severity.OK

    def test_above_threshold(self):
        pulls = [
            _pr(
                login="renovate[bot]",
                created_at=datetime.now(UTC) - timedelta(days=i),
                url=f"https://github.com/org/repo/pull/{i}",
                number=i,
            )
            for i in range(5)
        ]
        result = get_check("renovate_prs_piling").evaluate(
            _mock_repo(), _status(), config={}, context=_ctx(pulls=pulls)
        )
        assert result.severity == Severity.HIGH
        assert "(5) Renovate PRs" in result.summary
        assert result.link is not None

    def test_custom_threshold(self):
        pulls = [_pr(login="renovate[bot]") for _ in range(3)]
        result = get_check("renovate_prs_piling").evaluate(
            _mock_repo(),
            _status(),
            config={"renovate_pr_threshold": 3},
            context=_ctx(pulls=pulls),
        )
        assert result.severity == Severity.HIGH

    def test_mixed_authors(self):
        pulls = [
            _pr(login="renovate[bot]"),
            _pr(login="human-user"),
            _pr(login="renovate[bot]"),
            _pr(login="renovate[bot]"),
        ]
        result = get_check("renovate_prs_piling").evaluate(
            _mock_repo(), _status(), config={}, context=_ctx(pulls=pulls)
        )
        assert result.severity == Severity.HIGH
        assert "(3) Renovate PRs" in result.summary


class TestStalePRs:
    def test_no_stale(self):
        pulls = [_pr(created_at=datetime.now(UTC))]
        result = get_check("stale_prs").evaluate(
            _mock_repo(), _status(), config={}, context=_ctx(pulls=pulls)
        )
        assert result.severity == Severity.OK

    def test_stale_found(self):
        pulls = [
            _pr(
                created_at=datetime.now(UTC) - timedelta(days=10),
                url="https://github.com/org/repo/pull/1",
            ),
            _pr(created_at=datetime.now(UTC)),
        ]
        result = get_check("stale_prs").evaluate(
            _mock_repo(), _status(), config={}, context=_ctx(pulls=pulls)
        )
        assert result.severity == Severity.MEDIUM
        assert "(1) stale PR" in result.summary
        assert "10d" in result.summary
        assert result.link == "https://github.com/org/repo/pull/1"

    def test_custom_threshold(self):
        pulls = [_pr(created_at=datetime.now(UTC) - timedelta(days=3))]
        result = get_check("stale_prs").evaluate(
            _mock_repo(),
            _status(),
            config={"stale_pr_days": 2},
            context=_ctx(pulls=pulls),
        )
        assert result.severity == Severity.MEDIUM

    def test_empty_pulls(self):
        result = get_check("stale_prs").evaluate(
            _mock_repo(), _status(), config={}, context=_ctx(pulls=[])
        )
        assert result.severity == Severity.OK

    def test_renovate_prs_excluded(self):
        pulls = [
            _pr(login="renovate[bot]", created_at=datetime.now(UTC) - timedelta(days=30)),
            _pr(login="human-user", created_at=datetime.now(UTC)),
        ]
        result = get_check("stale_prs").evaluate(
            _mock_repo(), _status(), config={}, context=_ctx(pulls=pulls)
        )
        assert result.severity == Severity.OK

    def test_default_threshold_is_7_days(self):
        pulls = [
            _pr(
                created_at=datetime.now(UTC) - timedelta(days=6),
                url="https://github.com/org/repo/pull/1",
            ),
        ]
        result = get_check("stale_prs").evaluate(
            _mock_repo(), _status(), config={}, context=_ctx(pulls=pulls)
        )
        assert result.severity == Severity.OK


class TestOpenIssues:
    def test_below_threshold(self):
        result = get_check("open_issues").evaluate(
            _mock_repo(),
            _status(open_issues=5, human_open_issues=5),
            config={},
            context=_ctx(),
        )
        assert result.severity == Severity.OK

    def test_above_threshold(self):
        result = get_check("open_issues").evaluate(
            _mock_repo(),
            _status(open_issues=15, human_open_issues=15),
            config={},
            context=_ctx(),
        )
        assert result.severity == Severity.LOW
        assert "(15) open issues" in result.summary
        assert result.link is not None

    def test_bot_issues_excluded(self):
        # human_open_issues is pre-computed during the GraphQL fetch and
        # excludes bots. The check reads it straight off RepoStatus.
        result = get_check("open_issues").evaluate(
            _mock_repo(),
            _status(open_issues=12, human_open_issues=5),
            config={},
            context=_ctx(),
        )
        assert result.severity == Severity.OK
        assert "(5) open issues" in result.summary

    def test_custom_threshold(self):
        result = get_check("open_issues").evaluate(
            _mock_repo(),
            _status(open_issues=3, human_open_issues=3),
            config={"open_issues_threshold": 3},
            context=_ctx(),
        )
        assert result.severity == Severity.LOW


class TestOpenPRs:
    def test_no_prs(self):
        result = get_check("open_prs").evaluate(
            _mock_repo(), _status(open_prs=0), config={}, context=_ctx()
        )
        assert result.severity == Severity.OK

    def test_one_pr_triggers(self):
        result = get_check("open_prs").evaluate(
            _mock_repo(), _status(open_prs=1), config={}, context=_ctx()
        )
        assert result.severity == Severity.MEDIUM
        assert "(1) open PR" in result.summary
        # No pulls context available -> fall back to the listing page.
        assert result.link == "https://github.com/org/myrepo/pulls"

    def test_links_to_oldest_non_draft_pr(self):
        oldest = _pr(
            created_at=datetime.now(UTC) - timedelta(days=10),
            url="https://github.com/org/repo/pull/100",
        )
        newer = _pr(
            created_at=datetime.now(UTC) - timedelta(days=1),
            url="https://github.com/org/repo/pull/200",
        )
        draft = _pr(
            created_at=datetime.now(UTC) - timedelta(days=20),
            url="https://github.com/org/repo/pull/50",
            is_draft=True,
        )
        result = get_check("open_prs").evaluate(
            _mock_repo(),
            _status(open_prs=3, draft_prs=1),
            config={},
            context=_ctx(pulls=[newer, draft, oldest]),
        )
        assert result.severity == Severity.MEDIUM
        assert result.link == "https://github.com/org/repo/pull/100"

    def test_drafts_excluded(self):
        # Two open PRs but both are drafts -- should not flip yellow.
        result = get_check("open_prs").evaluate(
            _mock_repo(),
            _status(open_prs=2, draft_prs=2),
            config={},
            context=_ctx(),
        )
        assert result.severity == Severity.OK

    def test_non_draft_counted(self):
        result = get_check("open_prs").evaluate(
            _mock_repo(),
            _status(open_prs=3, draft_prs=2),
            config={},
            context=_ctx(),
        )
        assert result.severity == Severity.MEDIUM
        assert "(1) open PR" in result.summary

    def test_custom_threshold(self):
        result = get_check("open_prs").evaluate(
            _mock_repo(),
            _status(open_prs=1),
            config={"open_prs_threshold": 2},
            context=_ctx(),
        )
        assert result.severity == Severity.OK


class TestCoverageCheck:
    _STANDARD_PY = """\
jobs:
  unit-tests:
    name: Unit tests
    steps:
      - name: Checkout
        uses: actions/checkout@v6
      - name: Run pytest
        run: |
          uv run pytest --cov=src --cov-fail-under=80 --cov-report=xml
"""

    _LOWERED_PY = """\
jobs:
  unit-tests:
    steps:
      - name: Run pytest
        run: uv run pytest --cov=src --cov-fail-under=60
"""

    _NO_FAIL_UNDER_PY = """\
jobs:
  unit-tests:
    steps:
      - name: Run pytest
        run: uv run pytest --cov=src --cov-report=xml
"""

    _CONTINUE_ON_ERROR_PY = """\
jobs:
  unit-tests:
    steps:
      - name: Run pytest
        continue-on-error: true
        run: uv run pytest --cov=src --cov-fail-under=80
"""

    _OR_TRUE_PY = """\
jobs:
  unit-tests:
    steps:
      - name: Run pytest
        run: uv run pytest --cov=src --cov-fail-under=80 || true
"""

    _NODE_VITEST = """\
jobs:
  unit-tests:
    steps:
      - name: Vitest
        run: npx vitest run --coverage
"""

    _NO_UNIT_TESTS = """\
jobs:
  lint:
    steps:
      - run: echo hi
"""

    _NO_COVERAGE_COMMAND = """\
jobs:
  unit-tests:
    steps:
      - name: Run pytest
        run: uv run pytest -v
"""

    def _evaluate(self, text: str | None, *, path: str = ".github/workflows/pipeline.yaml"):
        ctx = _ctx(
            pipeline_path=path if text is not None else None,
            pipeline_text=text,
        )
        return get_check("coverage").evaluate(_mock_repo(), _status(), config={}, context=ctx)

    def test_standard_threshold_is_ok(self):
        result = self._evaluate(self._STANDARD_PY)
        assert result.severity == Severity.OK
        assert "80" in result.summary

    def test_lowered_threshold_is_low(self):
        result = self._evaluate(self._LOWERED_PY)
        assert result.severity == Severity.LOW
        assert "60" in result.summary
        assert "reduced" in result.summary
        assert result.link is not None
        assert "pipeline.yaml" in result.link

    def test_no_fail_under_is_low(self):
        result = self._evaluate(self._NO_FAIL_UNDER_PY)
        assert result.severity == Severity.LOW
        assert "fail-under" in result.summary

    def test_continue_on_error_is_medium(self):
        result = self._evaluate(self._CONTINUE_ON_ERROR_PY)
        assert result.severity == Severity.MEDIUM
        assert "ignored" in result.summary

    def test_or_true_suffix_is_medium(self):
        result = self._evaluate(self._OR_TRUE_PY)
        assert result.severity == Severity.MEDIUM
        assert "ignored" in result.summary

    def test_node_vitest_is_ok(self):
        result = self._evaluate(self._NODE_VITEST)
        assert result.severity == Severity.OK
        assert "coverage" in result.summary.lower()

    def test_missing_pipeline_is_ok_not_standardized(self):
        # A repo that doesn't follow the standardized pipeline convention
        # isn't unhealthy -- just not standardized. No severity.
        result = self._evaluate(None)
        assert result.severity == Severity.OK
        assert "not standardized" in result.summary

    def test_missing_unit_tests_job_is_ok(self):
        result = self._evaluate(self._NO_UNIT_TESTS)
        assert result.severity == Severity.OK
        assert "not standardized" in result.summary
        assert "unit-tests" in result.summary

    def test_missing_coverage_command_is_ok(self):
        result = self._evaluate(self._NO_COVERAGE_COMMAND)
        assert result.severity == Severity.OK
        assert "coverage" in result.summary

    def test_yml_fallback(self):
        result = self._evaluate(self._STANDARD_PY, path=".github/workflows/pipeline.yml")
        assert result.severity == Severity.OK

    def test_yaml_parse_error_is_medium(self):
        # Broken YAML is a real problem -- stays at MEDIUM severity.
        result = self._evaluate("jobs: [this is: not valid")
        assert result.severity == Severity.MEDIUM
        assert "parse error" in result.summary

    def test_lowest_threshold_wins(self):
        yaml_text = """\
jobs:
  unit-tests:
    steps:
      - run: uv run pytest --cov=src --cov-fail-under=80
      - run: uv run pytest tests/integration --cov=src --cov-fail-under=50
"""
        result = self._evaluate(yaml_text)
        assert result.severity == Severity.LOW
        assert "50" in result.summary


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class TestRunHealthChecks:
    def test_builds_context_when_none_given(self):
        # Passing context=None falls back to an empty FetchContext -- no
        # per-repo REST call is made.
        health = run_health_checks(_mock_repo(), _status())
        assert isinstance(health, RepoHealth)
        assert len(health.checks) == len(all_checks())

    def test_run_all_preserves_ordering(self):
        statuses = [
            _status(name="a", full_name="org/a"),
            _status(name="b", full_name="org/b"),
        ]
        repos = [_mock_repo(), _mock_repo()]
        healths = run_all_health_checks(repos, statuses)
        # Returns worst-first, not input order; both are OK so tie-breakers
        # don't matter -- we just assert both repos appear.
        names = {h.status.name for h in healths}
        assert names == {"a", "b"}
