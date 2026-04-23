"""Data model, on-disk cache, and REST fallbacks for the dashboard.

Most workspace data flows through the batched GraphQL fetcher in ``_gql.py``.
The REST entry points kept here are:

- Building a ``RepoStatus`` from a pre-fetched ``RepoSnapshot`` (no I/O).
- Looking up failing-run job/step detail for repos currently failing CI
  (``fetch_failing_run_detail``). GraphQL's ``statusCheckRollup`` doesn't
  expose failing-job names, so this is the last narrow REST call required.
- Disk-backed caching (``load_cache`` / ``save_cache``) which persists
  across restarts so the dashboard paints instantly from cache on boot.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from github.GithubException import GithubException

if TYPE_CHECKING:
    from github.Repository import Repository

    from ._gql import RepoSnapshot

CACHE_DIR = Path.home() / ".cache" / "ai-tools-dashboard"
CACHE_FILE = CACHE_DIR / "tui_cache.json"

# One-time migration from the old cache directory name.
_OLD_CACHE_DIR = Path.home() / ".cache" / "ai-gh"
if _OLD_CACHE_DIR.is_dir() and not CACHE_DIR.exists():
    _OLD_CACHE_DIR.rename(CACHE_DIR)

# Renovate's default "Dependency Dashboard" issue. Users don't treat it as a
# real issue -- it's a persistent control panel -- so the dashboard excludes
# it from open-issue counts. Customized titles (via dependencyDashboardTitle)
# aren't matched; those repos fall through to the real count.
_RENOVATE_DASHBOARD_TITLE = "Dependency Dashboard"


def _is_renovate_dashboard(issue) -> bool:
    """True if *issue* is Renovate's dependency-dashboard control-panel issue.

    Uses GraphQL's ``__typename == "Bot"`` signal. Login allowlists don't
    work because some Renovate installations report ``renovate`` as the
    login (no ``[bot]`` suffix) while GraphQL still types the author as
    ``Bot``.
    """
    title = (issue.title or "").strip()
    return title == _RENOVATE_DASHBOARD_TITLE and getattr(issue, "author_is_bot", False)


# ---------------------------------------------------------------------------
# Tag / framework detection from GraphQL-provided root-tree entries
# ---------------------------------------------------------------------------


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


def _detect_tags(
    primary_language: str | None, root_entries: tuple[str, ...]
) -> tuple[bool, tuple[str, ...]]:
    """Derive workspace-flag and framework/IaC tags from tree entry names."""
    tags: list[str] = []
    lang_tag = _LANG_MAP.get(primary_language or "")
    if lang_tag:
        tags.append(lang_tag)

    names = set(root_entries)
    is_workspace = "workspace.yaml" in names

    if "cdk.json" in names:
        tags.append("cdk")
    if "template.yaml" in names or "samconfig.toml" in names:
        tags.append("sam")
    if any(n.startswith("next.config") for n in names):
        tags.append("next")
    elif any(n.startswith("vite.config") for n in names):
        tags.append("vite")

    if "main.tf" in names or "terraform" in names:
        tags.append("tf")

    return is_workspace, tuple(tags)


# Root-tree files that mark a repo as a deployable service. Detected
# independently of branch layout so the dashboard can flag a service-shaped repo
# whose dev branch has gone missing -- the actual configuration drift, not a
# downstream symptom in the rollup state.
#
# ``Dockerfile`` is intentionally absent: many Python libraries ship a
# Dockerfile for the dev container or CI runner without ever being deployed.
# A SAM/CDK/Serverless config or a JS/web build are the unambiguous "this gets
# deployed somewhere" signals.
_SERVICE_MARKERS: tuple[str, ...] = (
    "template.yaml",
    "template.yml",
    "samconfig.toml",
    "serverless.yml",
    "serverless.yaml",
    "cdk.json",
)


def _is_org_repo(name: str) -> bool:
    """AWS Organization IaC repos: name ends with ``-org``.

    Org repos hold CloudFormation/SAM templates that manage the AWS Organization
    itself (accounts, OUs, SCPs, StackSets). They are deployed to a single
    account, never run a dev/main split, and may also publish convenience
    Python packages off main. They are neither services nor libraries in the
    standard sense and should be excluded from both classifications.
    """
    return name.endswith("-org")


def _detect_service_markers(name: str, root_entries: tuple[str, ...]) -> tuple[str, ...]:
    """Return the subset of service-marker files indicating a deployable service.

    Returns an empty tuple for repos that look like *something other than* a
    deployable service even when service markers are present:

    - **Org repos** (``*-org``): IaC for an AWS Organization, no dev/main split.
    - **Workspace repos** (``workspace.yaml``): meta-repos that coordinate other
      repos, never deployed themselves.
    - **Python packages** (``pyproject.toml`` without ``package.json``):
      libraries published to PyPI that frequently ship a SAM template purely
      for ephemeral test environments or CI infrastructure, not production
      deploys.
    """
    if _is_org_repo(name):
        return ()
    names = set(root_entries)
    if "workspace.yaml" in names:
        return ()
    if "pyproject.toml" in names and "package.json" not in names:
        return ()
    return tuple(marker for marker in _SERVICE_MARKERS if marker in names)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class RepoStatus:
    name: str
    full_name: str
    # Whether a ``dev`` branch exists on the repo. Drives the dev/main column
    # rendering and the "dev pipeline failing" branch of broken_ci. Independent
    # of whether the repo is *structurally* a service -- see ``looks_like_service``.
    has_dev_branch: bool
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
    # Whether the repo is private on GitHub.
    private: bool = False
    # Human-filed open issues (bots + PRs filtered out).
    human_open_issues: int = 0
    # ISO-8601 UTC creation timestamp of the oldest human-filed open issue.
    # Drives the "stale" / "ancient" tint on the counts line and the
    # open_issues health-check severity escalation.
    oldest_issue_created_at: str | None = None
    # Default branch name, used by checks that build links to files on GitHub.
    default_branch: str = "main"
    # Whether the repo has at least one workflow file checked in. Lets
    # broken_ci distinguish "truly no CI configured" from "latest commit
    # happened not to trigger any workflow" (common for semantic-release
    # chore commits that intentionally skip CI).
    has_workflows: bool = False
    # Structural service detection: true when the repo root contains any
    # canonical service-marker file (template.yaml, samconfig.toml,
    # serverless.yml, cdk.json, Dockerfile, ...). Independent of branch layout
    # so the dashboard can flag service-shaped repos whose dev branch is missing.
    looks_like_service: bool = False
    # Specific service-marker filenames detected at the repo root, surfaced in
    # diagnostic output for the missing-dev-branch alert.
    service_markers: tuple[str, ...] = ()
    # AWS Organization IaC repo (name ends with ``-org``). Holds templates for
    # the org itself and may publish convenience packages, but never runs a
    # dev/main split -- excluded from the service-missing-dev alert.
    is_org: bool = False


# ---------------------------------------------------------------------------
# Build RepoStatus from a GraphQL snapshot
# ---------------------------------------------------------------------------


def _to_iso_utc(when) -> str | None:
    if when is None:
        return None
    try:
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        return str(when.astimezone(UTC).isoformat())
    except Exception:
        return None


def build_status_from_snapshot(
    snapshot: RepoSnapshot,
    *,
    main_error: str | None = None,
    dev_error: str | None = None,
    main_failing_since: str | None = None,
    dev_failing_since: str | None = None,
) -> RepoStatus:
    """Project a ``RepoSnapshot`` into the flat ``RepoStatus`` widgets consume.

    Branch-level failure *detail* (``*_error`` and ``*_failing_since``) isn't
    available from GraphQL's ``statusCheckRollup``; the caller fills it in via
    ``fetch_failing_run_detail`` for any branch whose rollup reports failure.
    """
    from ._gql import translate_rollup_state

    open_prs = snapshot.pr_total_count
    draft_prs = sum(1 for pr in snapshot.pull_requests if pr.is_draft)

    # Filter human issues from the subset returned in the snapshot. The
    # snapshot caps at 100 issues per repo; repos with more will report the
    # full total as open_issues but only the first 100 contribute to the
    # human-filtered count. The warning threshold (default 10) is comfortably
    # below that cap.
    human_issues = [i for i in snapshot.issues if not getattr(i, "author_is_bot", False)]
    human_open_issues = len(human_issues)
    oldest = min((i.created_at for i in human_issues), default=None)
    oldest_iso = _to_iso_utc(oldest)

    # Total open issues (including bots) -- prefer the GraphQL totalCount
    # since it's authoritative even when the node list was truncated.
    # Subtract Renovate's Dependency Dashboard (at most one per repo) so a
    # repo with only that control-panel issue open displays as zero.
    dashboard_count = sum(1 for i in snapshot.issues if _is_renovate_dashboard(i))
    open_issues = max(0, snapshot.issue_total_count - dashboard_count)

    is_workspace, tags = _detect_tags(snapshot.primary_language, snapshot.root_entries)
    service_markers = _detect_service_markers(snapshot.name, snapshot.root_entries)
    is_org = _is_org_repo(snapshot.name)

    main_status = translate_rollup_state(snapshot.main_rollup_state)
    dev_status: str | None = (
        translate_rollup_state(snapshot.dev_rollup_state) if snapshot.has_dev_branch else None
    )

    return RepoStatus(
        name=snapshot.name,
        full_name=snapshot.full_name,
        has_dev_branch=snapshot.has_dev_branch,
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
        private=snapshot.is_private,
        human_open_issues=human_open_issues,
        oldest_issue_created_at=oldest_iso,
        default_branch=snapshot.default_branch or "main",
        has_workflows=bool(snapshot.workflow_files),
        looks_like_service=bool(service_markers),
        service_markers=service_markers,
        is_org=is_org,
    )


# ---------------------------------------------------------------------------
# Failing-run REST fallback (only called for repos currently failing CI)
# ---------------------------------------------------------------------------


def _get_failed_step(run) -> str | None:
    """Describe the first failed job/step from a workflow run, if available."""
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


def fetch_failing_run_detail(repo: Repository, branch: str) -> tuple[str | None, str | None]:
    """REST lookup for the most recent failing run's error + timestamp.

    Called only for branches whose GraphQL rollup reports FAILURE, so total
    call count is proportional to failing repos (usually 0-2 per refresh),
    not repo count.
    """
    try:
        runs = repo.get_workflow_runs(branch=branch, exclude_pull_requests=True)  # type: ignore[arg-type]
        try:
            run = runs[0]
        except (IndexError, GithubException):
            return None, None
        if run.conclusion in ("failure", "timed_out", "action_required"):
            error = _get_failed_step(run)
            when = getattr(run, "updated_at", None) or getattr(run, "run_started_at", None)
            return error, _to_iso_utc(when)
    except GithubException:
        pass
    return None, None


# ---------------------------------------------------------------------------
# Disk-backed cache
# ---------------------------------------------------------------------------


def load_cache() -> dict[str, RepoStatus]:
    """Load cached repo statuses from disk."""
    if not CACHE_FILE.exists():
        return {}
    try:
        data = json.loads(CACHE_FILE.read_text())
        # Tolerate cache files written by older versions that don't know
        # about newer optional fields.
        allowed = {f.name for f in fields(RepoStatus)}
        result = {}
        for key, val in data.get("repos", {}).items():
            filtered = {k: v for k, v in val.items() if k in allowed}
            if "tags" in filtered and isinstance(filtered["tags"], list):
                filtered["tags"] = tuple(filtered["tags"])
            if "service_markers" in filtered and isinstance(filtered["service_markers"], list):
                filtered["service_markers"] = tuple(filtered["service_markers"])
            result[key] = RepoStatus(**filtered)
        return result
    except (json.JSONDecodeError, TypeError, KeyError):
        return {}


def save_cache(
    statuses: list[RepoStatus],
    healths: list | None = None,
    owners: list[str] | None = None,
) -> None:
    """Persist repo statuses and optional health data to disk.

    When *owners* is provided, ``repo_list`` (derived from *statuses*) and
    ``owners`` are written into the cache so the dashboard can warm-start
    without waiting for GitHub auth.  When *owners* is ``None``, any existing
    ``repo_list`` / ``owners`` values are carried forward from the current
    cache file (same pattern as health preservation).
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data: dict = {"repos": {s.full_name: asdict(s) for s in statuses}}

    if healths:
        data["health"] = {h.status.full_name: h.to_dict() for h in healths}
        data["health_ts"] = datetime.now(UTC).isoformat()

    if owners is not None:
        data["repo_list"] = [s.full_name for s in statuses]
        data["owners"] = owners

    existing: dict = {}
    if CACHE_FILE.exists():
        try:
            existing = json.loads(CACHE_FILE.read_text())
        except (json.JSONDecodeError, KeyError):
            existing = {}

    if not healths and "health" in existing:
        data["health"] = existing["health"]
        data["health_ts"] = existing.get("health_ts")

    if owners is None and "repo_list" in existing:
        data["repo_list"] = existing["repo_list"]
        data["owners"] = existing.get("owners")

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


def load_cache_timestamp() -> datetime | None:
    """Return the timestamp of the cached health data, or ``None``."""
    if not CACHE_FILE.exists():
        return None
    try:
        data = json.loads(CACHE_FILE.read_text())
        ts = data.get("health_ts")
        if not ts:
            return None
        parsed = datetime.fromisoformat(ts)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except (json.JSONDecodeError, TypeError, KeyError, ValueError):
        return None


def load_cache_context() -> dict | None:
    """Return ``{"repo_list": [...], "owners": [...]}`` from the cache file.

    Returns ``None`` if the cache file does not exist or does not contain
    both ``repo_list`` and ``owners`` keys (e.g. written by an older version).
    """
    if not CACHE_FILE.exists():
        return None
    try:
        data = json.loads(CACHE_FILE.read_text())
        if "repo_list" not in data or "owners" not in data:
            return None
        return {"repo_list": data["repo_list"], "owners": data["owners"]}
    except (json.JSONDecodeError, TypeError, KeyError):
        return None
