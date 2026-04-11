"""Workspace-level standardization drift aggregator.

Shells out to ``ai-shell standardize repo --verify <path>`` for each child
repository in dependency order, parses each output into a normalized
per-section status, and aggregates the results into a single workspace-level
drift report. Used by the ``ai-tools workspace standardize --verify`` CLI.

Parsing contract (T6-3): ai-shell emits stable text output. Each section
lives on its own line in the form::

    [PASS|DRIFT|FAIL] <section>: <detail>

Long details may wrap onto continuation lines that begin with leading
whitespace; those are appended to the previous section's detail. ai-shell
exits 0 on drift (drift is not a failure). Non-zero exit means ai-shell
itself broke (bad path, missing config, etc.) and the child is recorded as
``overall=error``.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from augint_tools.config import RepoConfig, WorkspaceConfig
from augint_tools.execution.workspace import get_repo_path, topological_sort

# Canonical section names emitted by ``ai-shell standardize repo --verify``.
# Used only for reference — the parser accepts any section name ai-shell
# produces, but the human formatter walks this ordering when it knows the
# sections.
SECTION_NAMES: tuple[str, ...] = (
    "detect",
    "pipeline",
    "precommit",
    "renovate",
    "release",
    "dotfiles",
    "repo_settings",
    "rulesets",
    "oidc",
)

# One line per section in ai-shell's verify output:
#   [PASS] detect: python/library
#   [DRIFT] pipeline: missing: Code quality, Security, ...
_LINE_RE = re.compile(r"^\[(PASS|DRIFT|FAIL)\]\s+(\w+):\s*(.*?)\s*$")

_STATUS_MAP: dict[str, str] = {
    "PASS": "pass",
    "DRIFT": "drift",
    "FAIL": "fail",
}

# Subprocess timeout per child (seconds). Verify is supposed to be fast; if a
# single child takes longer than this something is wrong.
_VERIFY_TIMEOUT_SECS = 180


@dataclass
class SectionResult:
    """Normalized verify result for one section of one repo."""

    status: str  # "pass" | "drift" | "fail"
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"status": self.status, "detail": self.detail}


@dataclass
class RepoVerifyResult:
    """Verify result for a single child repository."""

    name: str
    path: str
    present: bool
    overall: str  # "pass" | "drift" | "fail" | "error"
    sections: dict[str, SectionResult] = field(default_factory=dict)
    error: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "path": self.path,
            "present": self.present,
            "overall": self.overall,
            "sections": {name: section.to_dict() for name, section in self.sections.items()},
        }
        if self.error is not None:
            data["error"] = self.error
        if self.warnings:
            data["warnings"] = list(self.warnings)
        return data


@dataclass
class WorkspaceVerifyResult:
    """Aggregated verify result across every child in a workspace."""

    workspace_name: str
    repos_dir: str
    order: list[str]
    order_source: str  # "depends_on" | "declaration"
    repos: list[RepoVerifyResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def aggregate(self) -> dict[str, int]:
        repos_clean = sum(1 for r in self.repos if r.overall == "pass")
        repos_drift = sum(1 for r in self.repos if r.overall == "drift")
        repos_fail = sum(1 for r in self.repos if r.overall == "fail")
        repos_error = sum(1 for r in self.repos if r.overall == "error")
        total_sections_drift = sum(
            1 for r in self.repos for s in r.sections.values() if s.status == "drift"
        )
        total_sections_fail = sum(
            1 for r in self.repos for s in r.sections.values() if s.status == "fail"
        )
        return {
            "repos_checked": len(self.repos),
            "repos_clean": repos_clean,
            "repos_drift": repos_drift,
            "repos_fail": repos_fail,
            "repos_error": repos_error,
            "total_sections_drift": total_sections_drift,
            "total_sections_fail": total_sections_fail,
        }

    @property
    def status(self) -> str:
        """Workspace-level status: ok | drift | error.

        "error" wins over "drift" wins over "ok". A single child failing to
        run (couldn't spawn ai-shell, unparseable output, missing path, etc.)
        flips the whole workspace into "error" so CI treats it as a hard
        failure — drift is recoverable, a broken verify tool is not.
        """
        agg = self.aggregate
        if agg["repos_error"] > 0:
            return "error"
        if agg["repos_drift"] > 0 or agg["repos_fail"] > 0:
            return "drift"
        return "ok"

    @property
    def exit_code(self) -> int:
        """0 clean, 1 drift, 2 error. Matches ticket T6-1 acceptance."""
        mapping = {"ok": 0, "drift": 1, "error": 2}
        return mapping.get(self.status, 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace": {"name": self.workspace_name, "repos_dir": self.repos_dir},
            "order": self.order,
            "order_source": self.order_source,
            "repos": [r.to_dict() for r in self.repos],
            "aggregate": self.aggregate,
        }


def _filter_and_order(
    config: WorkspaceConfig, only: list[str] | None
) -> tuple[list[RepoConfig], str]:
    """Return (ordered_repos, order_source) after applying --only filter.

    Topological sort over the full repo set first, then filter — this keeps
    dependency order stable even when the user asks for a subset that spans
    multiple dependency tiers.
    """
    try:
        ordered = topological_sort(config.repos)
        order_source = "depends_on"
    except ValueError:
        # Cycle detected — fall back to declaration order.
        ordered = list(config.repos)
        order_source = "declaration"

    # If nobody declared any depends_on, treat the source as "declaration" so
    # humans reading the report aren't misled into thinking the ordering is
    # meaningful.
    if all(not r.depends_on for r in config.repos):
        order_source = "declaration"

    if only:
        allowed = set(only)
        ordered = [r for r in ordered if r.name in allowed]

    return ordered, order_source


def _run_ai_shell_verify(repo_path: Path) -> tuple[int, str, str]:
    """Invoke ``ai-shell standardize repo --verify <path>``.

    Returns (exit_code, stdout, stderr). Does NOT cd into the child — we pass
    the path as an argument so the parent's resolved ``augint-shell`` version
    stays in effect (see ticket T6-1 note on the uv shared-venv downgrade
    trap).

    Note (T6-3): ai-shell does **not** accept ``--json`` on this subcommand.
    The return is always text; parse it with :func:`_parse_verify_text`.
    """
    cmd = [
        "ai-shell",
        "standardize",
        "repo",
        "--verify",
        str(repo_path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_VERIFY_TIMEOUT_SECS,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return -1, "", "ai-shell executable not found on PATH"
    except subprocess.TimeoutExpired:
        return -1, "", f"ai-shell verify timed out after {_VERIFY_TIMEOUT_SECS}s"


def _parse_verify_text(
    stdout: str,
) -> tuple[dict[str, SectionResult], list[str], str | None]:
    """Parse ai-shell's ``--verify`` text output into per-section results.

    Returns ``(sections, warnings, error)``:

    - ``sections`` — dict keyed by section name (whatever ai-shell emitted)
    - ``warnings`` — non-fatal parsing issues (unparseable lines, duplicate
      sections). The caller surfaces these to the user but does NOT fail the
      child.
    - ``error`` — a fatal parse error (empty output, no recognizable
      sections). The caller marks the child ``overall=error`` when this is
      non-None.

    Handles continuation lines that begin with whitespace by appending them
    to the previous section's detail. This is defensive — real ai-shell
    output tends to fit on one line, but nothing in the contract forbids
    wrapping.
    """
    if not stdout.strip():
        return {}, [], "ai-shell produced no output"

    sections: dict[str, SectionResult] = {}
    warnings: list[str] = []
    current: str | None = None

    for raw_line in stdout.splitlines():
        # Blank lines are never meaningful.
        if not raw_line.strip():
            continue

        match = _LINE_RE.match(raw_line)
        if match:
            status_raw, section_name, detail = match.groups()
            status = _STATUS_MAP[status_raw]
            if section_name in sections:
                warnings.append(
                    f"section {section_name!r} appeared multiple times in ai-shell output; "
                    "last occurrence wins"
                )
            sections[section_name] = SectionResult(status=status, detail=detail.strip())
            current = section_name
            continue

        # Continuation line: starts with whitespace and belongs to the most
        # recently parsed section. Concatenate onto its detail.
        if current is not None and raw_line[:1] in (" ", "\t"):
            previous = sections[current]
            extra = raw_line.strip()
            merged = f"{previous.detail} {extra}".strip() if previous.detail else extra
            sections[current] = SectionResult(status=previous.status, detail=merged)
            continue

        # Anything else is noise — truncate for the warning so we don't dump
        # a huge stray stderr blob into the response.
        snippet = raw_line.strip()
        if len(snippet) > 120:
            snippet = snippet[:117] + "..."
        warnings.append(f"unparseable line in ai-shell output: {snippet}")

    if not sections:
        return {}, warnings, "no recognizable sections in ai-shell output"

    return sections, warnings, None


def _overall_from_sections(sections: dict[str, SectionResult]) -> str:
    """Derive a single-repo overall status from its section results."""
    statuses = {s.status for s in sections.values()}
    if "fail" in statuses:
        return "fail"
    if "drift" in statuses:
        return "drift"
    if statuses == {"pass"}:
        return "pass"
    # Defensive: empty sections or unrecognized statuses. Shouldn't happen
    # because the caller already errored out if parsing produced nothing.
    return "error"


def _verify_one_repo(workspace_root: Path, repo_config: RepoConfig) -> RepoVerifyResult:
    """Verify a single child repository."""
    repo_path = get_repo_path(workspace_root, repo_config)
    rel_path = str(repo_config.path)

    if not repo_path.exists():
        return RepoVerifyResult(
            name=repo_config.name,
            path=rel_path,
            present=False,
            overall="error",
            error="repository path does not exist",
        )

    exit_code, stdout, stderr = _run_ai_shell_verify(repo_path)

    if exit_code == -1:
        # Subprocess spawn / timeout failure — stderr carries the explanation.
        return RepoVerifyResult(
            name=repo_config.name,
            path=rel_path,
            present=True,
            overall="error",
            error=stderr.strip() or "ai-shell failed to launch",
        )

    if exit_code != 0:
        # ai-shell itself errored (bad path, missing config, etc.). Prefer
        # stderr, fall back to stdout, fall back to the exit code number.
        detail = stderr.strip() or stdout.strip() or f"ai-shell exited {exit_code}"
        return RepoVerifyResult(
            name=repo_config.name,
            path=rel_path,
            present=True,
            overall="error",
            error=detail,
        )

    sections, parse_warnings, parse_error = _parse_verify_text(stdout)
    if parse_error is not None:
        return RepoVerifyResult(
            name=repo_config.name,
            path=rel_path,
            present=True,
            overall="error",
            error=parse_error,
            warnings=parse_warnings,
        )

    overall = _overall_from_sections(sections)
    return RepoVerifyResult(
        name=repo_config.name,
        path=rel_path,
        present=True,
        overall=overall,
        sections=sections,
        warnings=parse_warnings,
    )


def verify_workspace(
    workspace_root: Path,
    config: WorkspaceConfig,
    only: list[str] | None = None,
) -> WorkspaceVerifyResult:
    """Run ai-shell verify across every child and aggregate the results.

    Args:
        workspace_root: Absolute path to the workspace directory (where
            ``workspace.yaml`` lives).
        config: Parsed workspace config.
        only: Optional list of repo names to restrict the run to. Unknown
            names are silently skipped.

    Returns:
        An aggregated :class:`WorkspaceVerifyResult`.
    """
    ordered, order_source = _filter_and_order(config, only)
    result = WorkspaceVerifyResult(
        workspace_name=config.name,
        repos_dir=config.repos_dir,
        order=[r.name for r in ordered],
        order_source=order_source,
    )

    for repo_config in ordered:
        repo_result = _verify_one_repo(workspace_root, repo_config)
        result.repos.append(repo_result)
        if repo_result.overall == "error" and repo_result.error:
            result.errors.append(f"{repo_result.name}: {repo_result.error}")
        for warn in repo_result.warnings:
            result.warnings.append(f"{repo_result.name}: {warn}")

    return result
