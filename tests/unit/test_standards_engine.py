"""Tests for the YAML compliance engine and its handler registry."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from augint_tools.dashboard.health import FetchContext, Severity
from augint_tools.dashboard.health._engine import (
    EngineOptions,
    clear_cache,
    run_engine,
)
from augint_tools.dashboard.health._handlers import (
    all_handlers,
    register_handler,
)
from augint_tools.dashboard.health.checks.yaml_engine import YamlEngineCheck


@pytest.fixture(autouse=True)
def _reset_cache():
    clear_cache()
    yield
    clear_cache()


def _ctx(**kwargs: Any) -> FetchContext:
    return FetchContext(**kwargs)


# ---------------------------------------------------------------------------
# Built-in check type: file_exists / file_absent
# ---------------------------------------------------------------------------


def test_file_exists_when_present():
    ctx = _ctx(pyproject_text="[project]\nname='x'")
    doc = {
        "checks": [
            {
                "id": "has.pyproject",
                "name": "pyproject.toml present",
                "severity": "HIGH",
                "check": {"type": "file_exists", "file": "pyproject.toml"},
            }
        ]
    }
    results = _run_with_doc(doc, ctx)
    assert results[0].severity == Severity.OK


def test_file_absent_when_missing():
    ctx = _ctx(pyproject_text=None)
    doc = {
        "checks": [
            {
                "id": "no.pyproject",
                "severity": "LOW",
                "check": {"type": "file_absent", "file": "pyproject.toml"},
            }
        ]
    }
    results = _run_with_doc(doc, ctx)
    assert results[0].severity == Severity.OK


# ---------------------------------------------------------------------------
# Built-in check type: file_content_matches
# ---------------------------------------------------------------------------


def test_file_content_matches_min_value_pass():
    ctx = _ctx(pyproject_text="[tool.coverage.report]\nfail_under = 85\n")
    doc = {
        "checks": [
            {
                "id": "coverage.threshold",
                "severity": "LOW",
                "check": {
                    "type": "file_content_matches",
                    "file": "pyproject.toml",
                    "pattern": r"fail_under\s*=\s*(\d+)",
                    "assert": {"type": "min_value", "value": 80},
                },
            }
        ]
    }
    results = _run_with_doc(doc, ctx)
    assert results[0].severity == Severity.OK


def test_file_content_matches_min_value_fail():
    ctx = _ctx(pyproject_text="[tool.coverage.report]\nfail_under = 50\n")
    doc = {
        "checks": [
            {
                "id": "coverage.threshold",
                "severity": "LOW",
                "check": {
                    "type": "file_content_matches",
                    "file": "pyproject.toml",
                    "pattern": r"fail_under\s*=\s*(\d+)",
                    "assert": {"type": "min_value", "value": 80},
                },
            }
        ]
    }
    results = _run_with_doc(doc, ctx)
    assert results[0].severity == Severity.LOW
    assert "50 < 80" in results[0].summary


def test_file_content_matches_pattern_absent():
    ctx = _ctx(pyproject_text="[project]\n")
    doc = {
        "checks": [
            {
                "id": "coverage.threshold",
                "severity": "LOW",
                "check": {
                    "type": "file_content_matches",
                    "file": "pyproject.toml",
                    "pattern": r"fail_under\s*=\s*(\d+)",
                    "assert": {"type": "present"},
                },
            }
        ]
    }
    results = _run_with_doc(doc, ctx)
    assert results[0].severity == Severity.LOW


# ---------------------------------------------------------------------------
# Built-in check type: workflow_job_has_step
# ---------------------------------------------------------------------------


_PIPELINE_WITH_CFNNAG = """\
jobs:
  security:
    steps:
      - name: SAST
        run: bandit src/
      - name: IaC policy
        run: cfn-nag_scan --input-path template.yaml
"""

_PIPELINE_WITHOUT_CFNNAG = """\
jobs:
  security:
    steps:
      - name: SAST
        run: bandit src/
"""


def test_workflow_job_has_step_pass():
    ctx = _ctx(pipeline_text=_PIPELINE_WITH_CFNNAG)
    doc = {
        "checks": [
            {
                "id": "security.iac_policy",
                "severity": "HIGH",
                "check": {
                    "type": "workflow_job_has_step",
                    "job": "security",
                    "step_matches": {"run_contains_any": ["cfn-nag", "cdk-nag"]},
                },
            }
        ]
    }
    results = _run_with_doc(doc, ctx)
    assert results[0].severity == Severity.OK


def test_workflow_job_has_step_missing():
    ctx = _ctx(pipeline_text=_PIPELINE_WITHOUT_CFNNAG)
    doc = {
        "checks": [
            {
                "id": "security.iac_policy",
                "severity": "HIGH",
                "check": {
                    "type": "workflow_job_has_step",
                    "job": "security",
                    "step_matches": {"run_contains_any": ["cfn-nag", "cdk-nag"]},
                },
            }
        ]
    }
    results = _run_with_doc(doc, ctx)
    assert results[0].severity == Severity.HIGH


# ---------------------------------------------------------------------------
# Built-in check type: workflow_all_jobs_scan (no-cheating)
# ---------------------------------------------------------------------------


_PIPELINE_CLEAN = """\
jobs:
  unit-tests:
    steps:
      - run: pytest --cov=src --cov-fail-under=80
"""

_PIPELINE_WITH_CHEAT = """\
jobs:
  unit-tests:
    steps:
      - run: pytest --cov=src --cov-fail-under=80 || true
"""

_PIPELINE_WITH_COE = """\
jobs:
  unit-tests:
    continue-on-error: true
    steps:
      - run: pytest
"""


def test_no_cheating_clean_pipeline():
    ctx = _ctx(pipeline_text=_PIPELINE_CLEAN)
    doc = {
        "checks": [
            {
                "id": "workflow.no_cheating",
                "severity": "HIGH",
                "check": {"type": "workflow_all_jobs_scan"},
            }
        ]
    }
    results = _run_with_doc(doc, ctx)
    assert results[0].severity == Severity.OK


def test_no_cheating_detects_pipe_true():
    ctx = _ctx(pipeline_text=_PIPELINE_WITH_CHEAT)
    doc = {
        "checks": [
            {
                "id": "workflow.no_cheating",
                "severity": "HIGH",
                "check": {"type": "workflow_all_jobs_scan"},
            }
        ]
    }
    results = _run_with_doc(doc, ctx)
    assert results[0].severity == Severity.HIGH
    assert "|| true" in results[0].summary


def test_no_cheating_detects_continue_on_error():
    ctx = _ctx(pipeline_text=_PIPELINE_WITH_COE)
    doc = {
        "checks": [
            {
                "id": "workflow.no_cheating",
                "severity": "HIGH",
                "check": {"type": "workflow_all_jobs_scan"},
            }
        ]
    }
    results = _run_with_doc(doc, ctx)
    assert results[0].severity == Severity.HIGH
    assert "continue-on-error" in results[0].summary


# ---------------------------------------------------------------------------
# Built-in check type: ruleset_has_required_checks
# ---------------------------------------------------------------------------


def test_ruleset_missing_required_check():
    rs = [
        {
            "name": "library",
            "target": "BRANCH",
            "rules": {
                "nodes": [
                    {
                        "type": "REQUIRED_STATUS_CHECKS",
                        "parameters": {
                            "required_status_checks": [
                                {"context": "Code quality"},
                                {"context": "Unit tests"},
                            ]
                        },
                    }
                ]
            },
        }
    ]
    ctx = _ctx(rulesets=rs)
    doc = {
        "checks": [
            {
                "id": "ruleset.required_checks",
                "severity": "HIGH",
                "check": {
                    "type": "ruleset_has_required_checks",
                    "expected_contexts": [
                        "Code quality",
                        "Security",
                        "Unit tests",
                        "Build validation",
                        "Compliance",
                    ],
                },
            }
        ]
    }
    results = _run_with_doc(doc, ctx)
    assert results[0].severity == Severity.HIGH
    assert "Security" in results[0].summary
    assert "Build validation" in results[0].summary


# ---------------------------------------------------------------------------
# Handler dispatch
# ---------------------------------------------------------------------------


@register_handler("_test_always_pass")
def _always_pass(_context, _params):
    return True, "ok"


@register_handler("_test_always_fail")
def _always_fail(_context, _params):
    return False, "nope"


def test_handler_pass():
    ctx = _ctx()
    doc = {
        "checks": [
            {
                "id": "x.pass",
                "severity": "HIGH",
                "check": {"type": "handler", "name": "_test_always_pass"},
            }
        ]
    }
    results = _run_with_doc(doc, ctx)
    assert results[0].severity == Severity.OK


def test_handler_fail_preserves_severity():
    ctx = _ctx()
    doc = {
        "checks": [
            {
                "id": "x.fail",
                "severity": "CRITICAL",
                "check": {"type": "handler", "name": "_test_always_fail"},
            }
        ]
    }
    results = _run_with_doc(doc, ctx)
    assert results[0].severity == Severity.CRITICAL


def test_handler_unknown_name():
    ctx = _ctx()
    doc = {
        "checks": [
            {
                "id": "x.missing",
                "severity": "MEDIUM",
                "check": {"type": "handler", "name": "does_not_exist"},
            }
        ]
    }
    results = _run_with_doc(doc, ctx)
    assert results[0].severity == Severity.MEDIUM
    assert "not registered" in results[0].summary


# ---------------------------------------------------------------------------
# Template substitution + applies_to filtering
# ---------------------------------------------------------------------------


def test_applies_to_filters_by_repo_tag():
    ctx = _ctx()
    doc = {
        "checks": [
            {
                "id": "library_only",
                "applies_to": ["library"],
                "check": {"type": "handler", "name": "_test_always_pass"},
            },
            {
                "id": "service_only",
                "applies_to": ["service"],
                "check": {"type": "handler", "name": "_test_always_pass"},
            },
        ]
    }
    results = _run_with_doc(doc, ctx, tags={"library"})
    ids = {r.check_name for r in results}
    assert "library_only" in ids
    assert "service_only" not in ids


def test_template_substitution_in_link():
    ctx = _ctx(owner="myorg", repo_name="myrepo")
    doc = {
        "checks": [
            {
                "id": "x.fail",
                "severity": "HIGH",
                "link": "https://github.com/{owner}/{repo_name}/actions",
                "check": {"type": "handler", "name": "_test_always_fail"},
            }
        ]
    }
    results = _run_with_doc(doc, ctx)
    assert results[0].link == "https://github.com/myorg/myrepo/actions"


# ---------------------------------------------------------------------------
# Handler registry integrity
# ---------------------------------------------------------------------------


def test_builtin_handlers_are_registered():
    handlers = all_handlers()
    assert "aws_oidc_trust_policy_scope" in handlers
    assert "http_health_probe" in handlers
    assert "lambda_deploy_sha_match" in handlers


# ---------------------------------------------------------------------------
# Engine without network (gh=None) returns informational result
# ---------------------------------------------------------------------------


def test_engine_without_gh_returns_single_info():
    ctx = _ctx()
    options = EngineOptions(standards_url=None, handlers=all_handlers())
    results = run_engine(ctx, options, gh=None, repo_tags={"library"}, default_branch="main")
    assert len(results) == 1
    assert results[0].severity == Severity.OK
    assert "unavailable" in results[0].summary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_with_doc(doc: dict, ctx: FetchContext, tags: set[str] | None = None):
    """Invoke run_engine with a stub gh that returns ``doc`` as the YAML."""
    gh = _stub_gh_for_doc(doc)
    options = EngineOptions(standards_url="stub://standards.yaml", handlers=all_handlers())
    return run_engine(ctx, options, gh, tags or {"library"}, "main")


def _stub_gh_for_doc(doc: dict):
    """Build a MagicMock Github client whose requester returns ``doc`` as base64 YAML."""
    import base64

    import yaml as _yaml

    encoded = base64.b64encode(_yaml.safe_dump(doc).encode("utf-8")).decode("ascii")
    stub = MagicMock()
    requester = MagicMock()
    requester.requestJsonAndCheck.return_value = ({}, {"content": encoded, "encoding": "base64"})
    stub.requester = requester
    return stub


# ---------------------------------------------------------------------------
# Engine caching (YamlEngineCheck level)
# ---------------------------------------------------------------------------


class TestEngineCaching:
    """Verify the engine caches results by (SHA, rulesets) and invalidates correctly."""

    def _make_config(self, doc: dict) -> dict:
        """Build a config dict with a stubbed gh that returns ``doc``."""
        return {
            "standards_engine": {
                "gh": _stub_gh_for_doc(doc),
                "url": "stub://standards.yaml",
            },
        }

    def _make_status(self, full_name: str = "org/repo") -> MagicMock:
        s = MagicMock()
        s.full_name = full_name
        s.is_workspace = False
        s.looks_like_service = False
        s.is_org = False
        s.default_branch = "main"
        return s

    def test_same_sha_same_rulesets_returns_cached(self):
        doc = {
            "checks": [
                {"id": "x", "check": {"type": "handler", "name": "_test_always_pass"}},
            ]
        }
        check = YamlEngineCheck()
        config = self._make_config(doc)
        repo = MagicMock()
        status = self._make_status()
        ctx1 = _ctx(main_head_sha="abc123", rulesets=[{"name": "r1"}])
        ctx2 = _ctx(main_head_sha="abc123", rulesets=[{"name": "r1"}])

        r1 = check.evaluate(repo, status, config=config, context=ctx1)
        r2 = check.evaluate(repo, status, config=config, context=ctx2)
        # Same object returned from cache.
        assert r1 is r2

    def test_sha_change_invalidates_cache(self):
        doc = {
            "checks": [
                {"id": "x", "check": {"type": "handler", "name": "_test_always_pass"}},
            ]
        }
        check = YamlEngineCheck()
        config = self._make_config(doc)
        repo = MagicMock()
        status = self._make_status()
        ctx1 = _ctx(main_head_sha="abc123", rulesets=[{"name": "r1"}])
        ctx2 = _ctx(main_head_sha="def456", rulesets=[{"name": "r1"}])

        r1 = check.evaluate(repo, status, config=config, context=ctx1)
        r2 = check.evaluate(repo, status, config=config, context=ctx2)
        assert r1 is not r2

    def test_rulesets_change_invalidates_cache(self):
        doc = {
            "checks": [
                {"id": "x", "check": {"type": "handler", "name": "_test_always_pass"}},
            ]
        }
        check = YamlEngineCheck()
        config = self._make_config(doc)
        repo = MagicMock()
        status = self._make_status()
        ctx1 = _ctx(main_head_sha="abc123", rulesets=[{"name": "r1"}])
        ctx2 = _ctx(main_head_sha="abc123", rulesets=[{"name": "r1"}, {"name": "r2"}])

        r1 = check.evaluate(repo, status, config=config, context=ctx1)
        r2 = check.evaluate(repo, status, config=config, context=ctx2)
        assert r1 is not r2
