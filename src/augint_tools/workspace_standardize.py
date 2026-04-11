"""Workspace-level standardization drift aggregator.

Shells out to ``ai-shell standardize repo --verify <path>`` for each child
repository in dependency order, parses each output into a normalized
per-section status, and aggregates the results into a single workspace-level
drift report. Used by the ``ai-tools workspace standardize --verify`` CLI.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from augint_tools.config import RepoConfig, WorkspaceConfig
from augint_tools.execution.workspace import get_repo_path, topological_sort

# Section names emitted by ``ai-shell standardize repo --verify``. Ordering
# here drives the display order in the human-readable formatter.
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

        - "error" wins over "drift" wins over "ok". A single child failing to
          run (couldn't spawn ai-shell, invalid JSON, missing path, etc.)
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
    """Invoke ``ai-shell --json standardize repo --verify <path>``.

    Returns (exit_code, stdout, stderr). Does NOT cd into the child — we pass
    the path as an argument so the parent's resolved ``augint-shell`` version
    stays in effect (see ticket T6-1 note on the uv shared-venv downgrade
    trap).
    """
    cmd = [
        "ai-shell",
        "--json",
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


def _locate_sections(payload: Any) -> dict[str, Any] | None:
    """Find the sections dict inside an ai-shell JSON payload.

    We don't hardcode ai-shell's envelope shape because we can't guarantee it
    stays stable across releases. Instead, probe the usual spots in order of
    preference and pick the first one that looks like a sections dict.
    """
    candidates: list[Any] = []
    if isinstance(payload, dict):
        result = payload.get("result")
        if isinstance(result, dict):
            candidates.append(result.get("sections"))
            # Some envelopes put sections directly under result.
            candidates.append(result)
        candidates.append(payload.get("sections"))

    for cand in candidates:
        if isinstance(cand, dict) and any(
            key in cand and isinstance(cand[key], dict) for key in SECTION_NAMES
        ):
            return cand
    return None


def _parse_verify_output(stdout: str) -> tuple[dict[str, SectionResult], str | None]:
    """Parse ai-shell verify stdout into per-section results.

    Returns (sections, error_message). A non-None error means the output
    couldn't be parsed — the caller should mark the repo as ``error``.
    """
    stdout = stdout.strip()
    if not stdout:
        return {}, "ai-shell produced no output"

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return {}, f"ai-shell output is not valid JSON: {exc}"

    sections_raw = _locate_sections(payload)
    if sections_raw is None:
        return {}, "could not locate section results in ai-shell output"

    parsed: dict[str, SectionResult] = {}
    for name in SECTION_NAMES:
        entry = sections_raw.get(name)
        if not isinstance(entry, dict):
            continue
        raw_status = str(entry.get("status", "")).strip().lower()
        # Normalize ai-shell's statuses to our three canonical values. Unknown
        # statuses are promoted to "fail" so drift isn't silently dropped.
        if raw_status in ("pass", "ok"):
            status = "pass"
        elif raw_status == "drift":
            status = "drift"
        elif raw_status in ("fail", "error"):
            status = "fail"
        else:
            status = "fail"
        detail = str(entry.get("detail", "")).strip()
        parsed[name] = SectionResult(status=status, detail=detail)

    if not parsed:
        return {}, "no recognizable sections in ai-shell output"

    return parsed, None


def _overall_from_sections(sections: dict[str, SectionResult]) -> str:
    """Derive a single-repo overall status from its section results."""
    if any(s.status == "fail" for s in sections.values()):
        return "fail"
    if any(s.status == "drift" for s in sections.values()):
        return "drift"
    return "pass"


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
        # Subprocess spawn/timeout failure — stderr carries the explanation.
        return RepoVerifyResult(
            name=repo_config.name,
            path=rel_path,
            present=True,
            overall="error",
            error=stderr.strip() or "ai-shell failed to launch",
        )

    sections, parse_error = _parse_verify_output(stdout)
    if parse_error is not None:
        # If ai-shell exited non-zero on top of a parse failure, fold the
        # stderr in so the user sees both signals in one place.
        detail = parse_error
        if exit_code != 0 and stderr.strip():
            detail = f"{parse_error}; stderr: {stderr.strip()}"
        return RepoVerifyResult(
            name=repo_config.name,
            path=rel_path,
            present=True,
            overall="error",
            error=detail,
        )

    overall = _overall_from_sections(sections)
    return RepoVerifyResult(
        name=repo_config.name,
        path=rel_path,
        present=True,
        overall=overall,
        sections=sections,
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

    return result
