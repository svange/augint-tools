"""Health check: test-coverage threshold enforcement in CI.

Parses the repository's canonical GitHub Actions workflow
(``.github/workflows/pipeline.yaml``) and inspects the ``unit-tests`` job
for signals that the coverage gate has been **actively weakened**:

- Python: ``pytest --cov-fail-under=N`` with N below the canonical 80% baseline
  (see the ai-standardize-pipeline standard). Severity LOW.
- Any coverage-related step that ignores failures via ``continue-on-error: true``
  or shell suffixes such as ``|| true``, ``|| :``, ``|| echo ...``. Severity MEDIUM.
- Python ``pytest --cov`` with no ``--cov-fail-under`` gate at all. Severity LOW
  (equivalent to a 0% threshold).

**Non-adoption is benign.** Repos that haven't adopted the
ai-standardize-pipeline convention (no pipeline.yaml, or pipeline.yaml with
no ``unit-tests`` job, or unit-tests job with no coverage command) return
OK severity with an informative summary. This is observational, not a
warning -- a repo is not "unhealthy" just because it doesn't follow the
standard naming convention for its workflow files.

For Node repos using ``vitest``/``jest --coverage`` the threshold lives in
``vitest.config.*``/``jest.config.*`` rather than the workflow, so this check
is intentionally workflow-only and reports OK on seeing the coverage flag --
config-level parsing is out of scope.

Workflow text comes from the batched GraphQL snapshot via
``FetchContext.pipeline_text`` -- no per-repo REST call.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import yaml

from .._models import HealthCheckResult, Severity
from .._registry import register

if TYPE_CHECKING:
    from github.Repository import Repository

    from ..._data import RepoStatus
    from .. import FetchContext

# Canonical Python coverage threshold enforced by ai-standardize-pipeline.
_STANDARD_THRESHOLD = 80

# Regex for extracting ``--cov-fail-under=N`` or ``--cov-fail-under N``.
_FAIL_UNDER_RE = re.compile(r"--cov-fail-under[=\s]+(\d+)")

# Matches shell suffixes that swallow a non-zero exit code on the same line.
# ``|| true``, ``|| :``, ``|| echo "whatever"``.
_IGNORE_SUFFIX_RE = re.compile(r"\|\|\s*(true|:|echo\b)")


class CoverageCheck:
    """Detect lowered or disabled test-coverage gates in CI."""

    name = "coverage"
    description = "Detect lowered or ignored test-coverage thresholds in CI"

    def evaluate(
        self,
        repo: Repository,  # noqa: ARG002
        status: RepoStatus,
        *,
        config: dict,  # noqa: ARG002
        context: FetchContext,
    ) -> HealthCheckResult:
        default_branch = status.default_branch or "main"
        used_path = context.pipeline_path
        raw = context.pipeline_text

        # Missing / non-standard pipeline.yaml is benign -- it just means the
        # repo hasn't adopted the ai-standardize-pipeline convention. Surface
        # as OK (visible in the detail drawer) but never drives card coloring
        # because this is a "standards adoption" observation, not a health
        # warning.
        if raw is None or used_path is None:
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.OK,
                summary="not standardized (no pipeline.yaml)",
            )

        workflow_link = f"https://github.com/{status.full_name}/blob/{default_branch}/{used_path}"

        try:
            workflow = yaml.safe_load(raw)
        except yaml.YAMLError:
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.MEDIUM,
                summary="pipeline.yaml parse error",
                link=workflow_link,
            )

        if not isinstance(workflow, dict):
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.MEDIUM,
                summary="pipeline.yaml not a mapping",
                link=workflow_link,
            )

        jobs = workflow.get("jobs")
        if not isinstance(jobs, dict):
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.OK,
                summary="pipeline.yaml has no jobs",
            )

        unit_tests = jobs.get("unit-tests")
        if not isinstance(unit_tests, dict):
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.OK,
                summary="not standardized (no unit-tests job)",
            )

        steps = unit_tests.get("steps")
        if not isinstance(steps, list):
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.OK,
                summary="unit-tests has no steps",
            )

        return self._evaluate_steps(steps, workflow_link)

    def _evaluate_steps(self, steps: list[Any], workflow_link: str) -> HealthCheckResult:
        """Walk unit-tests steps and classify the coverage posture."""
        coverage_seen = False
        coverage_ignored = False
        lowered_threshold: int | None = None
        has_python_cov_flag = False
        has_python_fail_under = False
        has_node_coverage = False

        for step in steps:
            if not isinstance(step, dict):
                continue
            run = step.get("run")
            if not isinstance(run, str):
                continue

            is_python = "pytest" in run and "--cov" in run
            is_node = ("vitest" in run or "jest" in run) and "--coverage" in run
            if not (is_python or is_node):
                continue

            coverage_seen = True
            continue_on_error = bool(step.get("continue-on-error"))
            has_ignore_suffix = bool(_IGNORE_SUFFIX_RE.search(run))
            if continue_on_error or has_ignore_suffix:
                coverage_ignored = True

            if is_python:
                has_python_cov_flag = True
                match = _FAIL_UNDER_RE.search(run)
                if match:
                    has_python_fail_under = True
                    threshold = int(match.group(1))
                    # Track the lowest threshold seen across all matching steps.
                    if lowered_threshold is None or threshold < lowered_threshold:
                        lowered_threshold = threshold

            if is_node:
                has_node_coverage = True

        if not coverage_seen:
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.OK,
                summary="no coverage command in unit-tests",
            )

        # Ignoring failures is worse than lowering the bar: still run tests,
        # but a red coverage report has no consequence.
        if coverage_ignored:
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.MEDIUM,
                summary="coverage failures ignored",
                link=workflow_link,
            )

        if has_python_fail_under:
            assert lowered_threshold is not None
            if lowered_threshold < _STANDARD_THRESHOLD:
                return HealthCheckResult(
                    check_name=self.name,
                    severity=Severity.LOW,
                    summary=(
                        f"({lowered_threshold}%/{_STANDARD_THRESHOLD}%) "
                        f"code coverage reduced from standard"
                    ),
                    link=workflow_link,
                )
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.OK,
                summary=f"coverage threshold {lowered_threshold}%",
            )

        if has_python_cov_flag:
            # Coverage collected but no fail-under gate -- CI won't fail on
            # regressions. Treat as a lowered gate (equivalent to 0%).
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.LOW,
                summary="coverage reported but no fail-under threshold",
                link=workflow_link,
            )

        if has_node_coverage:
            # Workflow-only scope: threshold lives in vitest/jest config files
            # and is out of scope for this check. Trust the flag's presence.
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.OK,
                summary="coverage enforced (threshold in config)",
            )

        # Defensive fallback -- should be unreachable given the checks above.
        return HealthCheckResult(
            check_name=self.name,
            severity=Severity.OK,
            summary="coverage signal unclear",
        )


register(CoverageCheck())
