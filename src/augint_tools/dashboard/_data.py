"""Data fetching and on-disk caching for the dashboard.

Contains:
* ``RepoStatus`` -- the flat status record consumed by widgets.
* ``fetch_repo_status`` / ``_refresh`` -- pull the latest state from GitHub.
* ``load_cache`` / ``save_cache`` / ``load_health_cache`` -- disk-backed cache.
* ``has_dev_branch`` -- helper previously re-exported from ``config.py``.
"""

from __future__ import annotations

import json
import traceback
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path

from github.GithubException import GithubException
from github.Repository import Repository
from loguru import logger

CACHE_DIR = Path.home() / ".cache" / "ai-gh"
CACHE_FILE = CACHE_DIR / "tui_cache.json"


def has_dev_branch(repo: Repository) -> bool:
    """Check if the repository has a dev branch."""
    try:
        repo.get_branch("dev")
        return True
    except GithubException:
        return False


_LANG_MAP: dict[str, str] = {
    "Python": "py",
    "TypeScript": "ts",
    "JavaScript": "js",
    "Go": "go",
    "Rust": "rs",
    "Ruby": "rb",
    "Java": "java",
    "C#": "cs",
    "Shell": "sh",
    "HCL": "hcl",
    "Kotlin": "kt",
    "Swift": "swift",
}


def detect_repo_metadata(repo: Repository) -> tuple[bool, tuple[str, ...]]:
    """Detect workspace status and technology tags from repo metadata.

    Uses ``repo.language`` (free) and a single ``repo.get_contents("")`` call
    to scan root-level marker files for framework and IaC detection.

    Returns ``(is_workspace, tags)`` tuple.
    """
    tags: list[str] = []

    # Language from GitHub's auto-detection (no extra API call).
    lang = getattr(repo, "language", None) or ""
    lang_tag = _LANG_MAP.get(lang)
    if lang_tag:
        tags.append(lang_tag)

    # Scan root directory for marker files (1 API call).
    try:
        contents = repo.get_contents("")
        names = {c.name for c in contents} if isinstance(contents, list) else {contents.name}
    except GithubException:
        return False, tuple(tags)

    is_workspace = "workspace.yaml" in names

    # Framework detection.
    if "cdk.json" in names:
        tags.append("cdk")
    if "template.yaml" in names or "samconfig.toml" in names:
        tags.append("sam")
    if any(n.startswith("next.config") for n in names):
        tags.append("next")
    elif any(n.startswith("vite.config") for n in names):
        tags.append("vite")

    # IaC detection (terraform lives alongside frameworks, not elif).
    if "main.tf" in names or "terraform" in names:
        tags.append("tf")

    return is_workspace, tuple(tags)


@dataclass
class RepoStatus:
    name: str
    full_name: str
    is_service: bool
    main_status: str
    main_error: str | None
    dev_status: str | None
    dev_error: str | None
    open_issues: int
    open_prs: int
    draft_prs: int
    # ISO-8601 UTC timestamp of the most recent failing run on each branch.
    # Used to drive the "recently broken" border flash in the TUI (< 12h old).
    main_failing_since: str | None = None
    dev_failing_since: str | None = None
    # Workspace meta-repo (contains workspace.yaml).
    is_workspace: bool = False
    # Autodetected technology tags (e.g. "py", "sam", "tf").
    tags: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


def load_cache() -> dict[str, RepoStatus]:
    """Load cached repo statuses from disk."""
    if not CACHE_FILE.exists():
        return {}
    try:
        data = json.loads(CACHE_FILE.read_text())
        # Keep the loader tolerant of cache files written by older versions
        # that don't know about newer optional fields (e.g. *_failing_since).
        allowed = {f.name for f in fields(RepoStatus)}
        result = {}
        for key, val in data.get("repos", {}).items():
            filtered = {k: v for k, v in val.items() if k in allowed}
            # tags is stored as a JSON array but the dataclass expects a tuple.
            if "tags" in filtered and isinstance(filtered["tags"], list):
                filtered["tags"] = tuple(filtered["tags"])
            result[key] = RepoStatus(**filtered)
        return result
    except (json.JSONDecodeError, TypeError, KeyError):
        return {}


def save_cache(
    statuses: list[RepoStatus],
    healths: list | None = None,
) -> None:
    """Persist repo statuses and optional health data to disk."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data: dict = {"repos": {s.full_name: asdict(s) for s in statuses}}
    if healths:
        data["health"] = {h.status.full_name: h.to_dict() for h in healths}
        data["health_ts"] = datetime.now(UTC).isoformat()
    elif CACHE_FILE.exists():
        try:
            existing = json.loads(CACHE_FILE.read_text())
            if "health" in existing:
                data["health"] = existing["health"]
                data["health_ts"] = existing.get("health_ts")
        except (json.JSONDecodeError, KeyError):
            pass
    CACHE_FILE.write_text(json.dumps(data, indent=2))


def load_health_cache(
    statuses: dict[str, RepoStatus],
) -> dict:
    """Load cached health data. Returns dict of full_name -> RepoHealth."""
    if not CACHE_FILE.exists():
        return {}
    try:
        data = json.loads(CACHE_FILE.read_text())
        health_data = data.get("health", {})
        if not health_data:
            return {}
        from .health import RepoHealth

        result = {}
        for full_name, health_dict in health_data.items():
            if full_name in statuses:
                result[full_name] = RepoHealth.from_dict(statuses[full_name], health_dict)
        return result
    except (json.JSONDecodeError, TypeError, KeyError, ImportError):
        return {}


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------


def _get_failed_step(run):
    """Get a description of the first failed job/step from a workflow run."""
    try:
        jobs = run.jobs()
        for job in jobs:
            if job.conclusion == "failure":
                for step in job.steps:
                    if step.conclusion == "failure":
                        return f"{job.name}: {step.name}"
                return str(job.name)
    except (GithubException, AttributeError):
        pass
    return None


def get_run_status(repo: Repository, branch: str) -> tuple[str, str | None, str | None]:
    """Get the latest workflow run status and error info for a branch.

    Returns ``(status_string, error_description_or_None, failing_since_iso)``.
    ``failing_since_iso`` is the run's ``updated_at`` (UTC, ISO-8601) when
    the conclusion is a failure, otherwise ``None``. It lets the TUI decide
    whether a failure is recent enough to flash the card border.
    """
    try:
        runs = repo.get_workflow_runs(branch=branch, exclude_pull_requests=True)  # type: ignore[arg-type]
        if runs.totalCount == 0:
            return "unknown", None, None
        run = runs[0]
        if run.status in ("in_progress", "queued"):
            return "in_progress", None, None
        if run.conclusion == "success":
            return "success", None, None
        if run.conclusion in ("failure", "timed_out", "action_required"):
            error = _get_failed_step(run)
            when = getattr(run, "updated_at", None) or getattr(run, "run_started_at", None)
            failing_since = _to_iso_utc(when)
            return "failure", error, failing_since
        return "unknown", None, None
    except GithubException:
        return "unknown", None, None


def _to_iso_utc(when) -> str | None:
    """Best-effort conversion of a PyGithub datetime into an ISO-8601 UTC string."""
    if when is None:
        return None
    try:
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        return str(when.astimezone(UTC).isoformat())
    except Exception:
        return None


def fetch_repo_status(
    repo: Repository,
    previous: RepoStatus | None = None,
) -> RepoStatus:
    """Fetch status data for a single repository.

    On any unexpected error returns *previous* (stale data) when available,
    or a degraded placeholder so the dashboard never crashes.
    """
    status, _pulls = fetch_repo_status_with_pulls(repo, previous)
    return status


def fetch_repo_status_with_pulls(
    repo: Repository,
    previous: RepoStatus | None = None,
) -> tuple[RepoStatus, list]:
    """Fetch status and raw PR list for a single repository.

    Returns ``(status, pulls)`` so callers can pass the pulls list to
    health checks without re-fetching.
    """
    try:
        service = has_dev_branch(repo)
        is_workspace, tags = detect_repo_metadata(repo)
        main_status, main_error, main_failing_since = get_run_status(repo, repo.default_branch)
        if service:
            dev_status, dev_error, dev_failing_since = get_run_status(repo, "dev")
        else:
            dev_status, dev_error, dev_failing_since = None, None, None

        pulls_paged = repo.get_pulls(state="open")
        open_prs = pulls_paged.totalCount
        pulls_list = list(pulls_paged)
        draft_prs = sum(1 for pr in pulls_list if pr.draft)

        # open_issues_count includes PRs in GitHub's API
        open_issues = max(0, repo.open_issues_count - open_prs)

        return (
            RepoStatus(
                name=repo.name,
                full_name=repo.full_name,
                is_service=service,
                main_status=main_status,
                main_error=main_error,
                dev_status=dev_status,
                dev_error=dev_error,
                open_issues=open_issues,
                open_prs=open_prs,
                draft_prs=draft_prs,
                main_failing_since=main_failing_since,
                dev_failing_since=dev_failing_since,
                is_workspace=is_workspace,
                tags=tags,
            ),
            pulls_list,
        )
    except Exception as exc:
        logger.warning(f"fetch failed for {repo.full_name}: {exc.__class__.__name__}: {exc}")
        logger.debug(f"fetch traceback for {repo.full_name}: {traceback.format_exc()}")
        if previous is not None:
            return previous, []
        # Degraded placeholder -- keeps the dashboard alive
        return (
            RepoStatus(
                name=repo.name,
                full_name=repo.full_name,
                is_service=False,
                main_status="unknown",
                main_error="fetch error",
                dev_status=None,
                dev_error=None,
                open_issues=0,
                open_prs=0,
                draft_prs=0,
            ),
            [],
        )
