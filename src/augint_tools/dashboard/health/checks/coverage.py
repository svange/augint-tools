"""Health check: test-coverage threshold enforcement in CI.

Parses the repository's canonical GitHub Actions workflow
(``.github/workflows/pipeline.yaml``) and inspects the ``unit-tests`` job
for signals that the coverage gate has been weakened:

- Python: ``pytest --cov-fail-under=N`` with N below the canonical 80% baseline
  (see the ai-standardize-pipeline standard).
- Any coverage-related step that ignores failures via ``continue-on-error: true``
  or shell suffixes such as ``|| true``, ``|| :``, ``|| echo ...``.

For Node repos using ``vitest``/``jest --coverage`` the threshold lives in
``vitest.config.*``/``jest.config.*`` rather than the workflow, so this check
is intentionally workflow-only and reports OK on seeing the coverage flag --
config-level parsing is out of scope.

When the workflow can't be fetched or the ``unit-tests`` job / coverage step
is missing, the check returns MEDIUM severity with a summary prefixed
``unverified:`` so it is visibly distinguishable from a verified regression.
MEDIUM matches the precedent set by ``broken_ci`` for governance gaps and
avoids silently signalling OK for a repo we never actually validated.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import yaml
from github.GithubException import GithubException

from .._models import HealthCheckResult, Severity
from .._registry import register

if TYPE_CHECKING:
    from github.Repository import Repository

    from ..._data import RepoStatus

# Canonical Python coverage threshold enforced by ai-standardize-pipeline.
_STANDARD_THRESHOLD = 80

# Workflow file candidates, in preference order. The canonical name is
# ``pipeline.yaml`` (see the ai-standardize templates); ``pipeline.yml`` is a
# tolerated fallback for older repos that haven't been standardized yet.
_WORKFLOW_PATHS = (
    ".github/workflows/pipeline.yaml",
    ".github/workflows/pipeline.yml",
)

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
        repo: Repository,
        status: RepoStatus,
        *,
        config: dict,
        pulls: list | None = None,
    ) -> HealthCheckResult:
        try:
            default_branch = repo.default_branch
        except Exception:
            default_branch = "main"

        workflow_link = (
            f"https://github.com/{status.full_name}/blob/{default_branch}"
            "/.github/workflows/pipeline.yaml"
        )

        raw, used_path = self._fetch_workflow(repo, default_branch)
        if raw is None:
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.MEDIUM,
                summary="unverified: no pipeline.yaml",
                link=f"https://github.com/{status.full_name}",
            )

        try:
            workflow = yaml.safe_load(raw)
        except yaml.YAMLError:
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.MEDIUM,
                summary="unverified: pipeline.yaml parse error",
                link=workflow_link,
            )

        if not isinstance(workflow, dict):
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.MEDIUM,
                summary="unverified: pipeline.yaml not a mapping",
                link=workflow_link,
            )

        # Rebuild the link using the workflow path we actually found so clicks
        # land on the right file (pipeline.yaml vs pipeline.yml).
        workflow_link = f"https://github.com/{status.full_name}/blob/{default_branch}/{used_path}"

        jobs = workflow.get("jobs")
        if not isinstance(jobs, dict):
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.MEDIUM,
                summary="unverified: no jobs in pipeline.yaml",
                link=workflow_link,
            )

        unit_tests = jobs.get("unit-tests")
        if not isinstance(unit_tests, dict):
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.MEDIUM,
                summary="unverified: no unit-tests job",
                link=workflow_link,
            )

        steps = unit_tests.get("steps")
        if not isinstance(steps, list):
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.MEDIUM,
                summary="unverified: unit-tests has no steps",
                link=workflow_link,
            )

        return self._evaluate_steps(steps, workflow_link)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _fetch_workflow(
        self, repo: Repository, default_branch: str
    ) -> tuple[str | None, str | None]:
        """Fetch the first existing workflow file. Returns (text, path) or (None, None)."""
        for path in _WORKFLOW_PATHS:
            try:
                content_file = repo.get_contents(path, ref=default_branch)
            except GithubException:
                continue
            except Exception:
                # Any other failure (network, auth) is treated like "not found"
                # so the refresh loop stays alive; the caller returns UNVERIFIED.
                continue
            if isinstance(content_file, list):
                # Directory listing -- skip; shouldn't happen for a file path.
                continue
            try:
                raw = content_file.decoded_content.decode("utf-8")
            except Exception:
                continue
            return raw, path
        return None, None

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
                severity=Severity.MEDIUM,
                summary="unverified: no coverage command in unit-tests",
                link=workflow_link,
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
                        f"coverage threshold lowered to {lowered_threshold}% "
                        f"(std: {_STANDARD_THRESHOLD}%)"
                    ),
                    link=workflow_link,
                )
            return HealthCheckResult(
                check_name=self.name,
                severity=Severity.OK,
                summary=f"coverage threshold {lowered_threshold}%",
            )

        if has_python_cov_flag:
            # Coverage is collected but no fail-under gate -- the CI will not
            # fail on regressions. Treat as a lowered gate (equivalent to 0%).
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
            severity=Severity.MEDIUM,
            summary="unverified: coverage signal unclear",
            link=workflow_link,
        )


register(CoverageCheck())
