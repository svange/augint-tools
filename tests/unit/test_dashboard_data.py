"""Tests for dashboard._data cache + REST fallback helpers."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from github.GithubException import GithubException

from augint_tools.dashboard import _data
from augint_tools.dashboard._data import (
    RepoStatus,
    _detect_service_markers,
    _detect_tags,
    _get_failed_step,
    _is_org_repo,
    _to_iso_utc,
    fetch_failing_run_detail,
    load_cache,
    load_cache_timestamp,
    load_health_cache,
    save_cache,
)


def _status(name: str = "r", full_name: str | None = None) -> RepoStatus:
    return RepoStatus(
        name=name,
        full_name=full_name or f"org/{name}",
        has_dev_branch=False,
        main_status="ok",
        main_error=None,
        dev_status=None,
        dev_error=None,
        open_issues=0,
        open_prs=0,
        draft_prs=0,
    )


class TestDetectTags:
    def test_vite_branch(self):
        is_ws, tags = _detect_tags("TypeScript", ("vite.config.ts",))
        assert is_ws is False
        assert "ts" in tags and "vite" in tags

    def test_next_wins_over_vite(self):
        is_ws, tags = _detect_tags("JavaScript", ("next.config.js", "vite.config.js"))
        assert "next" in tags
        # elif chain means vite not added when next present.
        assert "vite" not in tags

    def test_terraform_folder_detected(self):
        _, tags = _detect_tags(None, ("terraform",))
        assert "tf" in tags

    def test_workspace_flag(self):
        is_ws, _ = _detect_tags("Python", ("workspace.yaml", "pyproject.toml"))
        assert is_ws is True


class TestServiceMarkers:
    def test_org_repo_excluded(self):
        assert _is_org_repo("my-org") is True
        assert _detect_service_markers("my-org", ("cdk.json",)) == ()

    def test_workspace_excluded(self):
        assert _detect_service_markers("anything", ("workspace.yaml", "cdk.json")) == ()

    def test_python_library_excluded(self):
        assert _detect_service_markers("lib", ("pyproject.toml", "template.yaml")) == ()

    def test_python_plus_package_json_counts_as_service(self):
        markers = _detect_service_markers(
            "svc", ("pyproject.toml", "package.json", "template.yaml")
        )
        assert markers == ("template.yaml",)

    def test_multiple_markers_preserved(self):
        markers = _detect_service_markers("svc", ("template.yaml", "cdk.json"))
        assert set(markers) == {"template.yaml", "cdk.json"}

    def test_nested_cdk_detected_as_service(self):
        """Repos with cdk.json in a subdirectory should be classified as services."""
        markers = _detect_service_markers(
            "svc", ("package.json",), nested_cdk_paths=("cdk/cdk.json",)
        )
        assert "cdk/cdk.json" in markers

    def test_python_with_nested_cdk_is_service(self):
        """Python repos with nested CDK infra must not be excluded as libraries."""
        markers = _detect_service_markers(
            "svc",
            ("pyproject.toml",),
            nested_cdk_paths=("infrastructure/cdk.json",),
        )
        assert "infrastructure/cdk.json" in markers

    def test_python_with_root_cdk_is_service(self):
        """Python repos with root cdk.json must not be excluded as libraries."""
        markers = _detect_service_markers("svc", ("pyproject.toml", "cdk.json"))
        assert "cdk.json" in markers

    def test_nested_cdk_excluded_for_org_repo(self):
        """Org repos are excluded even with nested CDK."""
        assert (
            _detect_service_markers(
                "my-org", ("pyproject.toml",), nested_cdk_paths=("cdk/cdk.json",)
            )
            == ()
        )

    def test_nested_cdk_excluded_for_workspace(self):
        """Workspace repos are excluded even with nested CDK."""
        assert (
            _detect_service_markers("svc", ("workspace.yaml",), nested_cdk_paths=("cdk/cdk.json",))
            == ()
        )


class TestDetectTagsNestedCdk:
    def test_nested_cdk_adds_cdk_tag(self):
        """Nested CDK paths should produce a 'cdk' tag."""
        _, tags = _detect_tags("Python", ("pyproject.toml",), nested_cdk_paths=("cdk/cdk.json",))
        assert "cdk" in tags

    def test_no_duplicate_cdk_tag_when_root_and_nested(self):
        """Root + nested CDK should produce exactly one 'cdk' tag."""
        _, tags = _detect_tags(
            "TypeScript",
            ("cdk.json", "package.json"),
            nested_cdk_paths=("infrastructure/cdk.json",),
        )
        assert tags.count("cdk") == 1


class TestToIsoUtc:
    def test_none_passthrough(self):
        assert _to_iso_utc(None) is None

    def test_naive_datetime_gets_utc_tz(self):
        naive = datetime(2026, 1, 2, 3, 4, 5)  # noqa: DTZ001 - exercising the tz-less path
        result = _to_iso_utc(naive)
        assert result is not None and result.endswith("+00:00")

    def test_aware_datetime_converted_to_utc(self):
        eastern = datetime(
            2026,
            1,
            2,
            12,
            0,
            0,
            tzinfo=timezone(_offset := __import__("datetime").timedelta(hours=-5)),
        )
        out = _to_iso_utc(eastern)
        # 12:00 EST -> 17:00 UTC.
        assert out is not None and "17:00:00" in out

    def test_non_datetime_returns_none(self):
        # Objects without tzinfo/astimezone fall through the except path.
        assert _to_iso_utc("not a datetime") is None


class TestGetFailedStep:
    def test_returns_job_and_step(self):
        job = MagicMock()
        job.name = "build"
        job.conclusion = "failure"
        failing_step = MagicMock(name="lint", conclusion="failure")
        failing_step.name = "lint"
        ok_step = MagicMock(name="setup", conclusion="success")
        job.steps = [ok_step, failing_step]
        run = MagicMock()
        run.jobs.return_value = [job]
        assert _get_failed_step(run) == "build: lint"

    def test_failing_job_without_failing_step_returns_job_name(self):
        job = MagicMock()
        job.name = "deploy"
        job.conclusion = "failure"
        job.steps = []  # no failing step -> fall back to job name
        run = MagicMock()
        run.jobs.return_value = [job]
        assert _get_failed_step(run) == "deploy"

    def test_no_failing_jobs_returns_none(self):
        job = MagicMock(conclusion="success")
        run = MagicMock()
        run.jobs.return_value = [job]
        assert _get_failed_step(run) is None

    def test_jobs_raises_github_exception(self):
        run = MagicMock()
        run.jobs.side_effect = GithubException(500, "boom", None)
        assert _get_failed_step(run) is None

    def test_jobs_missing_attribute(self):
        run = MagicMock()
        # AttributeError path via side_effect.
        run.jobs.side_effect = AttributeError("no jobs")
        assert _get_failed_step(run) is None


class TestFetchFailingRunDetail:
    def test_returns_error_and_timestamp(self):
        repo = MagicMock()
        run = MagicMock()
        run.conclusion = "failure"
        run.updated_at = datetime(2026, 1, 1, tzinfo=UTC)
        runs = MagicMock()
        runs.__getitem__ = lambda self, i: run if i == 0 else (_ for _ in ()).throw(IndexError())
        repo.get_workflow_runs.return_value = runs
        with patch("augint_tools.dashboard._data._get_failed_step", return_value="build: lint"):
            error, when = fetch_failing_run_detail(repo, "main")
        assert error == "build: lint"
        assert when is not None and when.startswith("2026-01-01")

    def test_falls_back_to_run_started_at(self):
        repo = MagicMock()
        run = MagicMock()
        run.conclusion = "timed_out"
        run.updated_at = None
        run.run_started_at = datetime(2026, 2, 2, tzinfo=UTC)
        runs = MagicMock()
        runs.__getitem__ = lambda self, i: run
        repo.get_workflow_runs.return_value = runs
        with patch("augint_tools.dashboard._data._get_failed_step", return_value=None):
            error, when = fetch_failing_run_detail(repo, "main")
        assert error is None
        assert when is not None and when.startswith("2026-02-02")

    def test_no_runs(self):
        repo = MagicMock()
        runs = MagicMock()
        runs.__getitem__ = MagicMock(side_effect=IndexError)
        repo.get_workflow_runs.return_value = runs
        assert fetch_failing_run_detail(repo, "main") == (None, None)

    def test_successful_run_ignored(self):
        repo = MagicMock()
        run = MagicMock(conclusion="success")
        runs = MagicMock()
        runs.__getitem__ = lambda self, i: run
        repo.get_workflow_runs.return_value = runs
        assert fetch_failing_run_detail(repo, "main") == (None, None)

    def test_github_exception_from_list_call(self):
        repo = MagicMock()
        repo.get_workflow_runs.side_effect = GithubException(500, "boom", None)
        assert fetch_failing_run_detail(repo, "main") == (None, None)

    def test_github_exception_on_index(self):
        repo = MagicMock()
        runs = MagicMock()
        runs.__getitem__ = MagicMock(side_effect=GithubException(500, "boom", None))
        repo.get_workflow_runs.return_value = runs
        assert fetch_failing_run_detail(repo, "main") == (None, None)


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    cache_file = tmp_path / "tui_cache.json"
    monkeypatch.setattr(_data, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(_data, "CACHE_FILE", cache_file)
    return cache_file


class TestCache:
    def test_load_cache_missing_file(self, isolated_cache):
        assert load_cache() == {}

    def test_save_then_load_roundtrip(self, isolated_cache):
        save_cache([_status("a"), _status("b")])
        loaded = load_cache()
        assert set(loaded.keys()) == {"org/a", "org/b"}
        assert isinstance(loaded["org/a"], RepoStatus)

    def test_load_cache_ignores_unknown_fields(self, isolated_cache):
        isolated_cache.write_text(
            json.dumps(
                {
                    "repos": {
                        "org/x": {
                            "name": "x",
                            "full_name": "org/x",
                            "has_dev_branch": False,
                            "main_status": "ok",
                            "main_error": None,
                            "dev_status": None,
                            "dev_error": None,
                            "open_issues": 0,
                            "open_prs": 0,
                            "draft_prs": 0,
                            "tags": ["py"],
                            "service_markers": ["cdk.json"],
                            "invented_future_field": "ignore me",
                        }
                    }
                }
            )
        )
        loaded = load_cache()
        assert "org/x" in loaded
        # list -> tuple coercion applied on known fields.
        assert loaded["org/x"].tags == ("py",)
        assert loaded["org/x"].service_markers == ("cdk.json",)

    def test_load_cache_malformed_json(self, isolated_cache):
        isolated_cache.write_text("not json")
        assert load_cache() == {}

    def test_save_cache_preserves_existing_health(self, isolated_cache):
        isolated_cache.parent.mkdir(parents=True, exist_ok=True)
        isolated_cache.write_text(
            json.dumps(
                {
                    "repos": {},
                    "health": {"org/a": {"findings": []}},
                    "health_ts": "2026-04-22T00:00:00+00:00",
                }
            )
        )
        save_cache([_status("a")])
        data = json.loads(isolated_cache.read_text())
        assert data["health"] == {"org/a": {"findings": []}}
        assert data["health_ts"] == "2026-04-22T00:00:00+00:00"

    def test_save_cache_with_fresh_health(self, isolated_cache):
        health = MagicMock()
        health.status.full_name = "org/a"
        health.to_dict.return_value = {"findings": ["x"]}
        save_cache([_status("a")], healths=[health])
        data = json.loads(isolated_cache.read_text())
        assert data["health"]["org/a"] == {"findings": ["x"]}
        assert "health_ts" in data

    def test_load_health_cache_missing(self, isolated_cache):
        assert load_health_cache({}) == {}

    def test_load_health_cache_no_health_section(self, isolated_cache):
        isolated_cache.write_text(json.dumps({"repos": {}}))
        assert load_health_cache({"org/a": _status("a")}) == {}

    def test_load_health_cache_malformed(self, isolated_cache):
        isolated_cache.write_text("broken")
        assert load_health_cache({"org/a": _status("a")}) == {}

    def test_load_cache_timestamp_missing_file(self, isolated_cache):
        assert load_cache_timestamp() is None

    def test_load_cache_timestamp_missing_field(self, isolated_cache):
        isolated_cache.write_text(json.dumps({"repos": {}}))
        assert load_cache_timestamp() is None

    def test_load_cache_timestamp_aware(self, isolated_cache):
        isolated_cache.write_text(
            json.dumps({"repos": {}, "health_ts": "2026-04-22T12:00:00+00:00"})
        )
        ts = load_cache_timestamp()
        assert ts is not None
        assert ts.tzinfo is UTC
        assert ts.hour == 12

    def test_load_cache_timestamp_naive_assumed_utc(self, isolated_cache):
        isolated_cache.write_text(json.dumps({"repos": {}, "health_ts": "2026-04-22T12:00:00"}))
        ts = load_cache_timestamp()
        assert ts is not None and ts.tzinfo is UTC

    def test_load_cache_timestamp_invalid(self, isolated_cache):
        isolated_cache.write_text(json.dumps({"repos": {}, "health_ts": "garbage"}))
        assert load_cache_timestamp() is None
