"""Batched GraphQL workspace fetcher.

Replaces the REST-heavy per-repo fetch loop with a single GraphQL query that
returns status, PRs, issues, root tree, Renovate config text, and pipeline
workflow text for every repo in the workspace. Reduces per-refresh API
pressure from ~10 REST calls per repo to ~1-2 GraphQL queries total.

The one thing GraphQL's ``statusCheckRollup`` does not give us is a failing
job/step name (for the "foo: bar failed" error detail). That remains on REST
in ``_data.py``, triggered only when a repo is actually failing -- a small
fraction of repos on any given refresh.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from loguru import logger
from requests.exceptions import ChunkedEncodingError, Timeout
from requests.exceptions import ConnectionError as RequestsConnectionError

if TYPE_CHECKING:
    from github import Github
    from github.Repository import Repository


# Canonical Renovate config paths. Must match RenovateEnabledCheck's probe list
# so the check can read config contents from the GraphQL snapshot instead of
# probing via REST.
RENOVATE_PATHS: tuple[str, ...] = (
    "renovate.json5",
    "renovate.json",
    ".github/renovate.json5",
    ".github/renovate.json",
    ".renovaterc",
    ".renovaterc.json",
)

# Pipeline workflow paths, canonical first. Mirrors the CoverageCheck probe list.
# Libraries use publish.yaml, services use deploy.yaml. Legacy pipeline.yaml
# is probed last for repos not yet re-standardized.
PIPELINE_PATHS: tuple[str, ...] = (
    ".github/workflows/publish.yaml",
    ".github/workflows/deploy.yaml",
    ".github/workflows/pipeline.yaml",
    ".github/workflows/pipeline.yml",
)

# File contents the YAML compliance engine needs. Each tuple is probed in
# order; the first non-null blob wins. Mirrors the (path, text) pattern
# already used for Renovate and pipeline.yaml.
PYPROJECT_PATHS: tuple[str, ...] = ("pyproject.toml",)
PACKAGE_JSON_PATHS: tuple[str, ...] = ("package.json",)
PRECOMMIT_PATHS: tuple[str, ...] = (".pre-commit-config.yaml", ".pre-commit-config.yml")
# Per-repo compliance engine overrides (opt-outs + handler param overrides).
# Placed by /ai-standardize-compliance; read by _engine.apply_overrides.
COMPLIANCE_PATHS: tuple[str, ...] = (".ai-compliance.yaml", ".ai-compliance.yml")

# Chunk size for repo batches per GraphQL query. GitHub's API has a per-query
# complexity budget of 500k nodes. With blob fields, PRs, and issues the
# fragment costs ~126 nodes per repo; 25 repos stays well under the cap.
# Rulesets are fetched via REST (separate rate-limit pool).
_BATCH_SIZE = 25

# How far back to walk the default-branch / dev-branch history looking for a
# non-null statusCheckRollup. Needed because semantic-release chore commits
# intentionally skip CI, leaving the tip commit's rollup null even when the
# pipeline is healthy. 5 covers the common case (a bump commit directly after
# the PR merge) with generous headroom.
_HISTORY_LOOKBACK = 5

# Transient network failures worth retrying. GraphQL/HTTP-level errors (4xx,
# rate-limit, schema) surface as other exception types and should NOT retry.
_TRANSIENT_NETWORK_ERRORS: tuple[type[Exception], ...] = (
    ChunkedEncodingError,
    RequestsConnectionError,
    Timeout,
)
_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = (0.5, 1.0)


# ---------------------------------------------------------------------------
# Response dataclasses
# ---------------------------------------------------------------------------


@dataclass
class PRSnapshot:
    """Subset of PR fields health checks need."""

    number: int
    is_draft: bool
    created_at: datetime
    author_login: str | None
    url: str


@dataclass
class IssueSnapshot:
    """Subset of Issue fields needed for human/bot counting."""

    number: int
    created_at: datetime
    author_login: str | None
    title: str | None = None
    # True when GraphQL resolved ``author`` to the ``Bot`` type. This covers
    # both canonical GitHub Apps (login ending in ``[bot]``) and self-hosted
    # Renovate setups whose author login is just ``renovate`` -- the
    # ``__typename`` is ``Bot`` in both cases.
    author_is_bot: bool = False


@dataclass
class RepoSnapshot:
    """All workspace data for one repo pulled from a single GraphQL query."""

    full_name: str
    name: str
    owner: str
    default_branch: str
    is_private: bool
    primary_language: str | None
    has_dev_branch: bool
    # Rollup state strings from GraphQL, not PyGithub's run.status vocabulary.
    # Translate at the call site via translate_rollup_state().
    main_rollup_state: str | None
    dev_rollup_state: str | None
    # Commit shas of the most recent commits on default / dev branches --
    # passed to the REST failing-run fallback so it can look up the exact run.
    main_head_sha: str | None
    dev_head_sha: str | None
    # Root-tree entry names for framework/IaC detection without extra REST calls.
    root_entries: tuple[str, ...]
    # Workflow filenames present under .github/workflows/ -- used by broken_ci
    # to distinguish "truly no CI configured" from "latest commit happened not
    # to trigger any workflow" (common for semantic-release chore commits).
    workflow_files: tuple[str, ...] = ()
    # Open PRs (up to 100 per repo; more would be extraordinary).
    pull_requests: list[PRSnapshot] = field(default_factory=list)
    pr_total_count: int = 0
    # Open issues (up to 100 per repo).
    issues: list[IssueSnapshot] = field(default_factory=list)
    issue_total_count: int = 0
    # Renovate config file contents keyed by canonical path, None when absent.
    renovate_configs: dict[str, str | None] = field(default_factory=dict)
    # Pipeline workflow contents keyed by canonical path, None when absent.
    pipeline_contents: dict[str, str | None] = field(default_factory=dict)
    # pyproject.toml contents (Python repos); keyed by canonical path.
    pyproject_contents: dict[str, str | None] = field(default_factory=dict)
    # package.json contents (Node repos); keyed by canonical path.
    package_json_contents: dict[str, str | None] = field(default_factory=dict)
    # .pre-commit-config.yaml contents; keyed by canonical path.
    precommit_contents: dict[str, str | None] = field(default_factory=dict)
    # .ai-compliance.yaml contents (per-repo engine overrides); keyed by path.
    compliance_contents: dict[str, str | None] = field(default_factory=dict)


@dataclass
class WorkspaceSnapshot:
    """Result of a batched workspace fetch."""

    by_full_name: dict[str, RepoSnapshot]
    # Total rate-limit cost of the queries that built this snapshot.
    rate_limit_cost: int = 0
    rate_limit_remaining: int = 0
    rate_limit_limit: int = 5000
    rate_limit_reset_at: datetime | None = None
    # Repos that couldn't be fetched (e.g. archived, renamed, permission errors).
    # Left to the caller to handle (typically by preserving previous state).
    errored: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Query construction
# ---------------------------------------------------------------------------


def _fragment() -> str:
    """GraphQL fragment covering every RepoSnapshot field."""
    renovate_fields = "\n".join(
        f'    _renovate_{i}: object(expression: "HEAD:{path}") {{ '
        f"... on Blob {{ text isTruncated }} }}"
        for i, path in enumerate(RENOVATE_PATHS)
    )
    pipeline_fields = "\n".join(
        f'    _pipeline_{i}: object(expression: "HEAD:{path}") {{ '
        f"... on Blob {{ text isTruncated }} }}"
        for i, path in enumerate(PIPELINE_PATHS)
    )
    pyproject_fields = "\n".join(
        f'    _pyproject_{i}: object(expression: "HEAD:{path}") {{ '
        f"... on Blob {{ text isTruncated }} }}"
        for i, path in enumerate(PYPROJECT_PATHS)
    )
    package_json_fields = "\n".join(
        f'    _package_json_{i}: object(expression: "HEAD:{path}") {{ '
        f"... on Blob {{ text isTruncated }} }}"
        for i, path in enumerate(PACKAGE_JSON_PATHS)
    )
    precommit_fields = "\n".join(
        f'    _precommit_{i}: object(expression: "HEAD:{path}") {{ '
        f"... on Blob {{ text isTruncated }} }}"
        for i, path in enumerate(PRECOMMIT_PATHS)
    )
    compliance_fields = "\n".join(
        f'    _compliance_{i}: object(expression: "HEAD:{path}") {{ '
        f"... on Blob {{ text isTruncated }} }}"
        for i, path in enumerate(COMPLIANCE_PATHS)
    )
    return f"""
fragment RepoFields on Repository {{
  nameWithOwner
  name
  owner {{ login }}
  isPrivate
  primaryLanguage {{ name }}
  defaultBranchRef {{
    name
    target {{
      ... on Commit {{
        oid
        statusCheckRollup {{ state }}
        history(first: {_HISTORY_LOOKBACK}) {{
          nodes {{ statusCheckRollup {{ state }} }}
        }}
      }}
    }}
  }}
  _dev: ref(qualifiedName: "refs/heads/dev") {{
    target {{
      ... on Commit {{
        oid
        statusCheckRollup {{ state }}
        history(first: {_HISTORY_LOOKBACK}) {{
          nodes {{ statusCheckRollup {{ state }} }}
        }}
      }}
    }}
  }}
  _main: ref(qualifiedName: "refs/heads/main") {{
    target {{
      ... on Commit {{
        oid
        statusCheckRollup {{ state }}
        history(first: {_HISTORY_LOOKBACK}) {{
          nodes {{ statusCheckRollup {{ state }} }}
        }}
      }}
    }}
  }}
  _rootTree: object(expression: "HEAD:") {{
    ... on Tree {{ entries {{ name }} }}
  }}
  _workflowsTree: object(expression: "HEAD:.github/workflows") {{
    ... on Tree {{ entries {{ name }} }}
  }}
  pullRequests(states: OPEN, first: 50) {{
    totalCount
    nodes {{
      number
      isDraft
      createdAt
      url
      author {{ login }}
    }}
  }}
  issues(states: OPEN, first: 50) {{
    totalCount
    nodes {{
      number
      title
      createdAt
      author {{ __typename login }}
    }}
  }}
{renovate_fields}
{pipeline_fields}
{pyproject_fields}
{package_json_fields}
{precommit_fields}
{compliance_fields}
}}
"""


def build_query(repos: list[Repository]) -> str:
    """Build a single-query batched workspace fetch for up to _BATCH_SIZE repos."""
    parts: list[str] = []
    for i, repo in enumerate(repos):
        owner, name = repo.full_name.split("/", 1)
        # Owner/name come from a GitHub API response -- no special-character
        # risk in practice, but be defensive against GraphQL string escapes.
        owner_esc = owner.replace("\\", "\\\\").replace('"', '\\"')
        name_esc = name.replace("\\", "\\\\").replace('"', '\\"')
        parts.append(
            f'  r{i}: repository(owner: "{owner_esc}", name: "{name_esc}") {{ ...RepoFields }}'
        )
    body = "\n".join(parts)
    return (
        "query WorkspaceSnapshot {\n"
        f"{body}\n"
        "  rateLimit { limit cost remaining resetAt }\n"
        "}\n"
        f"{_fragment()}"
    )


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


_ISO_FMT_FALLBACK = "%Y-%m-%dT%H:%M:%SZ"


def _parse_ts(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        # datetime.fromisoformat handles GitHub's ``Z`` suffix on Python 3.11+.
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        try:
            return datetime.strptime(value, _ISO_FMT_FALLBACK).replace(tzinfo=UTC)
        except ValueError:
            return None


def _resolve_rollup_state(target: Any) -> str | None:
    """Pick the first non-null rollup state from the tip commit or its ancestors.

    GraphQL's ``statusCheckRollup`` is null whenever the commit didn't trigger
    any workflow -- common on semantic-release chore commits and ``[skip ci]``
    merges. Falling back to ``history(first: _HISTORY_LOOKBACK)`` reflects the
    branch's actual pipeline health rather than whatever happened to land on
    the tip.
    """
    if not isinstance(target, dict):
        return None
    rollup = target.get("statusCheckRollup")
    if isinstance(rollup, dict) and rollup.get("state"):
        return str(rollup["state"])
    history = target.get("history")
    nodes = history.get("nodes") if isinstance(history, dict) else None
    if not isinstance(nodes, list):
        return None
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_rollup = node.get("statusCheckRollup")
        if isinstance(node_rollup, dict) and node_rollup.get("state"):
            return str(node_rollup["state"])
    return None


def _extract_blob_text(blob: dict | None) -> str | None:
    """Pull ``text`` out of a GraphQL Blob payload, respecting truncation.

    GitHub truncates blob text above 512KB; for Renovate configs and small
    pipeline.yaml files that's never an issue, but we still check because a
    pathological case (binary file masquerading as text) would otherwise
    surface as a silent half-read. Truncated content is treated as absent.
    """
    if not isinstance(blob, dict):
        return None
    if blob.get("isTruncated"):
        return None
    text = blob.get("text")
    return text if isinstance(text, str) else None


def _parse_repo(data: dict) -> RepoSnapshot:
    """Turn one repo's GraphQL payload into a RepoSnapshot."""
    full_name = str(data.get("nameWithOwner", ""))
    name = str(data.get("name", ""))
    owner_obj = data.get("owner") or {}
    owner = str(owner_obj.get("login", "")) if isinstance(owner_obj, dict) else ""

    default_branch_ref = data.get("defaultBranchRef") or {}
    default_branch = str(default_branch_ref.get("name", "main")) if default_branch_ref else "main"
    main_target = (default_branch_ref or {}).get("target") or {}
    main_rollup_state = _resolve_rollup_state(main_target)
    main_head_sha = main_target.get("oid") if isinstance(main_target, dict) else None

    dev_ref = data.get("_dev")
    has_dev_branch = dev_ref is not None
    dev_target = (dev_ref or {}).get("target") or {} if isinstance(dev_ref, dict) else {}
    dev_rollup_state = _resolve_rollup_state(dev_target)
    dev_head_sha = dev_target.get("oid") if isinstance(dev_target, dict) else None

    main_ref = data.get("_main")
    main_ref_target = (main_ref or {}).get("target") or {} if isinstance(main_ref, dict) else {}
    main_ref_rollup = _resolve_rollup_state(main_ref_target)
    main_ref_sha = main_ref_target.get("oid") if isinstance(main_ref_target, dict) else None

    # When ``dev`` is the default branch -- common on new projects that stage
    # on dev before cutting main, and on repos like aillc-web -- the
    # defaultBranchRef and the refs/heads/dev lookup alias the same commit.
    # Route the rollup into the dev slot and mark main as ABSENT so the card
    # renders "dev X  main N/A" (main styled informationally, not as a
    # failure). Using a sentinel ABSENT state lets broken_ci / severity
    # aggregation ignore it -- it's neither "failure" nor "unknown".
    if default_branch == "dev":
        dev_rollup_state = main_rollup_state
        dev_head_sha = main_head_sha
        has_dev_branch = True
        # Use the explicit main branch ref if it exists, otherwise ABSENT.
        if main_ref is not None and main_ref_rollup is not None:
            main_rollup_state = main_ref_rollup
            main_head_sha = main_ref_sha
        elif main_ref is not None:
            # main branch exists but has no CI rollup
            main_rollup_state = None  # will translate to "unknown"
            main_head_sha = main_ref_sha
        else:
            main_rollup_state = "ABSENT"
            main_head_sha = None

    root_tree = data.get("_rootTree")
    if isinstance(root_tree, dict):
        entries = root_tree.get("entries") or []
        root_entries = tuple(
            str(e.get("name", "")) for e in entries if isinstance(e, dict) and e.get("name")
        )
    else:
        root_entries = ()

    workflows_tree = data.get("_workflowsTree")
    if isinstance(workflows_tree, dict):
        wf_entries = workflows_tree.get("entries") or []
        workflow_files = tuple(
            str(e.get("name", ""))
            for e in wf_entries
            if isinstance(e, dict)
            and e.get("name")
            and str(e.get("name", "")).endswith((".yml", ".yaml"))
        )
    else:
        workflow_files = ()

    primary_language_obj = data.get("primaryLanguage")
    primary_language = (
        primary_language_obj.get("name") if isinstance(primary_language_obj, dict) else None
    )

    pr_data = data.get("pullRequests") or {}
    pr_total_count = int(pr_data.get("totalCount", 0)) if isinstance(pr_data, dict) else 0
    pulls: list[PRSnapshot] = []
    for pr in (pr_data.get("nodes") or []) if isinstance(pr_data, dict) else []:
        if not isinstance(pr, dict):
            continue
        author = pr.get("author") or {}
        author_login = author.get("login") if isinstance(author, dict) else None
        ts = _parse_ts(pr.get("createdAt")) or datetime.now(UTC)
        pulls.append(
            PRSnapshot(
                number=int(pr.get("number") or 0),
                is_draft=bool(pr.get("isDraft")),
                created_at=ts,
                author_login=author_login,
                url=str(pr.get("url") or ""),
            )
        )

    issue_data = data.get("issues") or {}
    issue_total_count = int(issue_data.get("totalCount", 0)) if isinstance(issue_data, dict) else 0
    issues: list[IssueSnapshot] = []
    for issue in (issue_data.get("nodes") or []) if isinstance(issue_data, dict) else []:
        if not isinstance(issue, dict):
            continue
        author = issue.get("author") or {}
        if isinstance(author, dict):
            author_login = author.get("login")
            author_is_bot = author.get("__typename") == "Bot"
        else:
            author_login = None
            author_is_bot = False
        ts = _parse_ts(issue.get("createdAt")) or datetime.now(UTC)
        title = issue.get("title")
        issues.append(
            IssueSnapshot(
                number=int(issue.get("number") or 0),
                created_at=ts,
                author_login=author_login,
                title=title if isinstance(title, str) else None,
                author_is_bot=author_is_bot,
            )
        )

    renovate_configs: dict[str, str | None] = {}
    for i, path in enumerate(RENOVATE_PATHS):
        renovate_configs[path] = _extract_blob_text(data.get(f"_renovate_{i}"))

    pipeline_contents: dict[str, str | None] = {}
    for i, path in enumerate(PIPELINE_PATHS):
        pipeline_contents[path] = _extract_blob_text(data.get(f"_pipeline_{i}"))

    pyproject_contents: dict[str, str | None] = {}
    for i, path in enumerate(PYPROJECT_PATHS):
        pyproject_contents[path] = _extract_blob_text(data.get(f"_pyproject_{i}"))

    package_json_contents: dict[str, str | None] = {}
    for i, path in enumerate(PACKAGE_JSON_PATHS):
        package_json_contents[path] = _extract_blob_text(data.get(f"_package_json_{i}"))

    precommit_contents: dict[str, str | None] = {}
    for i, path in enumerate(PRECOMMIT_PATHS):
        precommit_contents[path] = _extract_blob_text(data.get(f"_precommit_{i}"))

    compliance_contents: dict[str, str | None] = {}
    for i, path in enumerate(COMPLIANCE_PATHS):
        compliance_contents[path] = _extract_blob_text(data.get(f"_compliance_{i}"))

    return RepoSnapshot(
        full_name=full_name,
        name=name,
        owner=owner,
        default_branch=default_branch,
        is_private=bool(data.get("isPrivate", False)),
        primary_language=primary_language,
        has_dev_branch=has_dev_branch,
        main_rollup_state=main_rollup_state,
        dev_rollup_state=dev_rollup_state,
        main_head_sha=main_head_sha,
        dev_head_sha=dev_head_sha,
        root_entries=root_entries,
        workflow_files=workflow_files,
        pull_requests=pulls,
        pr_total_count=pr_total_count,
        issues=issues,
        issue_total_count=issue_total_count,
        renovate_configs=renovate_configs,
        pipeline_contents=pipeline_contents,
        pyproject_contents=pyproject_contents,
        package_json_contents=package_json_contents,
        precommit_contents=precommit_contents,
        compliance_contents=compliance_contents,
    )


def parse_response(
    response: dict, repos: list[Repository]
) -> tuple[dict[str, RepoSnapshot], dict[str, str], dict]:
    """Parse a GraphQL response into snapshots, errors, and rateLimit info.

    Returns ``(snapshots_by_full_name, errors_by_full_name, rate_limit)``.
    GraphQL ``errors`` at the top level are ignored for repos that still
    returned data; repos that couldn't be fetched (alias is null) land in
    the errors dict so the caller can preserve previous state.
    """
    data = response.get("data") or {}
    rate_limit = data.get("rateLimit") or {}
    snapshots: dict[str, RepoSnapshot] = {}
    errors: dict[str, str] = {}

    # Map top-level GraphQL errors back to their repo by ``path``.
    for err in response.get("errors") or []:
        path = err.get("path")
        if not isinstance(path, list) or not path:
            continue
        alias = path[0]
        if not isinstance(alias, str) or not alias.startswith("r"):
            continue
        try:
            idx = int(alias[1:])
        except ValueError:
            continue
        if 0 <= idx < len(repos):
            full_name = repos[idx].full_name
            errors[full_name] = str(err.get("message", "GraphQL error"))

    for i, repo in enumerate(repos):
        alias = f"r{i}"
        raw = data.get(alias)
        if not isinstance(raw, dict):
            errors.setdefault(repo.full_name, "repo payload missing from GraphQL response")
            continue
        try:
            snapshot = _parse_repo(raw)
        except Exception as exc:  # defensive -- parsing should never crash refresh
            errors[repo.full_name] = f"parse error: {exc.__class__.__name__}: {exc}"
            continue
        # The GraphQL response populates nameWithOwner directly, but fall back
        # to the requesting repo's full_name if the field is missing.
        if not snapshot.full_name:
            snapshot.full_name = repo.full_name
        snapshots[snapshot.full_name] = snapshot

    return snapshots, errors, rate_limit


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def _execute_query(gh: Github, query: str) -> dict:
    """POST a single GraphQL query via PyGithub's requester and return JSON.

    Retries on transient network failures (connection drops, incomplete
    chunked reads, timeouts). GraphQL-level errors and PyGithub exceptions
    are not retried -- they surface to the caller immediately.
    """
    # PyGithub's requester reuses the existing token / session, keeping us
    # from pulling in another HTTP client.
    requester = gh.requester
    last_exc: Exception | None = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            _headers, data = requester.requestJsonAndCheck(
                "POST", "/graphql", input={"query": query}
            )
            return data
        except _TRANSIENT_NETWORK_ERRORS as exc:
            last_exc = exc
            if attempt == _RETRY_ATTEMPTS - 1:
                break
            backoff = _RETRY_BACKOFF_SECONDS[attempt]
            logger.debug(
                "graphql transient network error ({}); retrying in {}s ({}/{})",
                exc.__class__.__name__,
                backoff,
                attempt + 1,
                _RETRY_ATTEMPTS - 1,
            )
            time.sleep(backoff)
    assert last_exc is not None
    raise last_exc


def fetch_workspace_snapshot(gh: Github, repos: list[Repository]) -> WorkspaceSnapshot:
    """Fetch snapshots for all repos via one or more batched GraphQL queries.

    Splits the repo list into chunks of ``_BATCH_SIZE`` to keep query
    complexity well under GitHub's cap. Aggregates the per-chunk rate-limit
    cost and errors into a single WorkspaceSnapshot.
    """
    by_full_name: dict[str, RepoSnapshot] = {}
    errored: dict[str, str] = {}
    total_cost = 0
    last_remaining = 0
    last_limit = 5000
    reset_at: datetime | None = None

    if not repos:
        return WorkspaceSnapshot(by_full_name={})

    for start in range(0, len(repos), _BATCH_SIZE):
        chunk = repos[start : start + _BATCH_SIZE]
        query = build_query(chunk)
        try:
            response = _execute_query(gh, query)
        except Exception as exc:
            logger.warning(
                "graphql workspace fetch failed for chunk {}-{}: {}: {}",
                start,
                start + len(chunk) - 1,
                exc.__class__.__name__,
                exc,
            )
            for repo in chunk:
                errored[repo.full_name] = f"graphql: {exc.__class__.__name__}: {exc}"
            continue

        snapshots, errors, rate_limit = parse_response(response, chunk)
        by_full_name.update(snapshots)
        errored.update(errors)

        if isinstance(rate_limit, dict):
            total_cost += int(rate_limit.get("cost") or 0)
            last_remaining = int(rate_limit.get("remaining") or last_remaining)
            last_limit = int(rate_limit.get("limit") or last_limit)
            reset_at = _parse_ts(rate_limit.get("resetAt")) or reset_at

    return WorkspaceSnapshot(
        by_full_name=by_full_name,
        rate_limit_cost=total_cost,
        rate_limit_remaining=last_remaining,
        rate_limit_limit=last_limit,
        rate_limit_reset_at=reset_at,
        errored=errored,
    )


# ---------------------------------------------------------------------------
# Helpers consumed by callers
# ---------------------------------------------------------------------------


def translate_rollup_state(state: str | None) -> str:
    """Map GraphQL ``StatusState`` to the existing dashboard vocabulary.

    The legacy REST path produced these strings; keeping them identical
    lets widgets and caches continue to work untouched.
    """
    if state in ("SUCCESS",):
        return "success"
    if state in ("FAILURE", "ERROR"):
        return "failure"
    if state in ("PENDING", "EXPECTED"):
        return "in_progress"
    # Parser sentinel: branch genuinely doesn't exist (e.g. no ``main`` on a
    # new project that's still on dev). Distinct from ``unknown``, which means
    # the branch exists but no rollup could be resolved.
    if state in ("ABSENT",):
        return "absent"
    # Missing rollup (e.g. no workflows) or unknown state.
    return "unknown"


def pick_renovate_config(snapshot: RepoSnapshot) -> tuple[str | None, str | None]:
    """Return the (path, text) of the first existing Renovate config, or (None, None)."""
    for path in RENOVATE_PATHS:
        text = snapshot.renovate_configs.get(path)
        if text and text.strip():
            return path, text
    return None, None


def _pick_first(
    contents: dict[str, str | None],
    paths: tuple[str, ...],
) -> tuple[str | None, str | None]:
    """Return the ``(path, text)`` of the first non-empty blob, or ``(None, None)``."""
    for path in paths:
        text = contents.get(path)
        if text and text.strip():
            return path, text
    return None, None


def pick_pipeline_yaml(snapshot: RepoSnapshot) -> tuple[str | None, str | None]:
    """Return the (path, text) of the first existing pipeline workflow, or (None, None)."""
    return _pick_first(snapshot.pipeline_contents, PIPELINE_PATHS)


def pick_pyproject(snapshot: RepoSnapshot) -> str | None:
    _, text = _pick_first(snapshot.pyproject_contents, PYPROJECT_PATHS)
    return text


def pick_package_json(snapshot: RepoSnapshot) -> str | None:
    _, text = _pick_first(snapshot.package_json_contents, PACKAGE_JSON_PATHS)
    return text


def pick_precommit(snapshot: RepoSnapshot) -> str | None:
    _, text = _pick_first(snapshot.precommit_contents, PRECOMMIT_PATHS)
    return text


def pick_compliance(snapshot: RepoSnapshot) -> str | None:
    _, text = _pick_first(snapshot.compliance_contents, COMPLIANCE_PATHS)
    return text


# ---------------------------------------------------------------------------
# Teams fetcher
# ---------------------------------------------------------------------------
#
# Teams are queried inverse to repos: GraphQL's Repository type has no direct
# ``teams`` connection, but organizations expose ``teams -> repositories`` with
# per-edge permission info. One query per org returns every team assignment
# across all repos in the workspace. Called on a slower cadence than the main
# refresh because team membership changes rarely -- see
# ``TeamsCache.is_stale``.


# Mirror of ``_TEAM_PERMISSION_ORDER`` in state.py -- kept here so _gql has
# no reverse dependency on state. Permission strings come straight from the
# GraphQL enum lowercased.
_TEAM_PERMISSION_ORDER: dict[str, int] = {
    "admin": 0,
    "maintain": 1,
    "write": 2,
    "triage": 3,
    "read": 4,
}


@dataclass
class TeamAssignment:
    """One team's access grant to one repo. Permission from the team->repo edge."""

    slug: str
    name: str
    permission: str  # "admin" | "maintain" | "write" | "triage" | "read"


@dataclass
class TeamsSnapshot:
    """Workspace-wide team data from one or more organization queries."""

    by_full_name: dict[str, list[TeamAssignment]] = field(default_factory=dict)
    # slug -> display name mapping for every team seen across all orgs.
    labels: dict[str, str] = field(default_factory=dict)
    rate_limit_cost: int = 0
    rate_limit_remaining: int = 0
    errored: dict[str, str] = field(default_factory=dict)  # keyed by owner login


def build_teams_query(owners: list[str]) -> str:
    """Build a single GraphQL query listing teams + repositories for each owner.

    Uses ``repositoryOwner`` + an inline fragment on ``Organization`` so a
    personal account (User owner) cleanly returns a null ``teams`` field
    instead of triggering a top-level ``Could not resolve to an Organization
    with the login of 'X'`` error. Only genuinely missing or inaccessible
    owners surface as errors.
    """
    parts: list[str] = []
    for i, owner in enumerate(owners):
        owner_esc = owner.replace("\\", "\\\\").replace('"', '\\"')
        parts.append(
            f'  o{i}: repositoryOwner(login: "{owner_esc}") {{\n'
            f"    __typename\n"
            f"    login\n"
            f"    ... on Organization {{\n"
            f"      teams(first: 50) {{\n"
            f"        nodes {{\n"
            f"          slug\n"
            f"          name\n"
            f"          repositories(first: 50) {{\n"
            f"            edges {{\n"
            f"              permission\n"
            f"              node {{ nameWithOwner }}\n"
            f"            }}\n"
            f"          }}\n"
            f"        }}\n"
            f"      }}\n"
            f"    }}\n"
            f"  }}"
        )
    body = "\n".join(parts)
    return f"query WorkspaceTeams {{\n{body}\n  rateLimit {{ limit cost remaining resetAt }}\n}}\n"


def parse_teams_response(response: dict, owners: list[str]) -> TeamsSnapshot:
    """Parse the WorkspaceTeams response into a TeamsSnapshot."""
    data = response.get("data") or {}
    rate_limit = data.get("rateLimit") or {}
    snapshot = TeamsSnapshot(
        rate_limit_cost=int(rate_limit.get("cost") or 0) if isinstance(rate_limit, dict) else 0,
        rate_limit_remaining=int(rate_limit.get("remaining") or 0)
        if isinstance(rate_limit, dict)
        else 0,
    )

    # Surface top-level errors per owner.
    for err in response.get("errors") or []:
        path = err.get("path")
        if not isinstance(path, list) or not path:
            continue
        alias = path[0]
        if not isinstance(alias, str) or not alias.startswith("o"):
            continue
        try:
            idx = int(alias[1:])
        except ValueError:
            continue
        if 0 <= idx < len(owners):
            snapshot.errored[owners[idx]] = str(err.get("message", "GraphQL error"))

    # Build per-repo assignments across every owner's team tree.
    assignments: dict[str, list[TeamAssignment]] = {}
    for i, owner in enumerate(owners):
        org_data = data.get(f"o{i}")
        # Personal-account owners return null -- that's fine, no teams to map.
        if not isinstance(org_data, dict):
            continue
        teams_conn = org_data.get("teams") or {}
        if not isinstance(teams_conn, dict):
            continue
        for team in teams_conn.get("nodes") or []:
            if not isinstance(team, dict):
                continue
            slug = str(team.get("slug") or "")
            if not slug:
                continue
            name = str(team.get("name") or slug)
            snapshot.labels[slug] = name
            repos_conn = team.get("repositories") or {}
            if not isinstance(repos_conn, dict):
                continue
            for edge in repos_conn.get("edges") or []:
                if not isinstance(edge, dict):
                    continue
                node = edge.get("node") or {}
                full_name = node.get("nameWithOwner") if isinstance(node, dict) else None
                if not isinstance(full_name, str):
                    continue
                permission = str(edge.get("permission") or "").lower()
                assignments.setdefault(full_name, []).append(
                    TeamAssignment(slug=slug, name=name, permission=permission)
                )
        # Note: if the owner IS an org but returned no teams, that's legitimate
        # empty state and doesn't warrant an error.
        _ = owner  # silences ruff ARG002 -- owner used above via i

    # Sort each repo's team list by (permission order, slug) so "primary" is
    # deterministic and matches what the REST-era collect_repo_teams produced.
    for full_name, team_list in assignments.items():
        team_list.sort(key=lambda t: (_TEAM_PERMISSION_ORDER.get(t.permission, 99), t.slug.lower()))
        snapshot.by_full_name[full_name] = team_list

    return snapshot


def fetch_workspace_teams(gh: Github, owners: list[str]) -> TeamsSnapshot:
    """One GraphQL query covering every owner's teams + repo assignments.

    Designed to be called on a cadence independent from ``fetch_workspace_snapshot``
    (teams change rarely; caching for several minutes is safe).
    """
    if not owners:
        return TeamsSnapshot()
    query = build_teams_query(owners)
    try:
        response = _execute_query(gh, query)
    except Exception as exc:
        logger.warning("graphql teams fetch failed: {}: {}", exc.__class__.__name__, exc)
        return TeamsSnapshot(
            errored=dict.fromkeys(owners, f"graphql: {exc.__class__.__name__}: {exc}")
        )
    return parse_teams_response(response, owners)
