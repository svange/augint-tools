"""Central state and pure reducers for the v2 dashboard."""

from __future__ import annotations

import colorsys
import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from github.GithubException import GithubException
from loguru import logger

from ._data import RepoStatus, load_cache, load_health_cache
from .health import RepoHealth, Severity

if TYPE_CHECKING:
    from github.Repository import Repository

    from .sysmeter import GpuStats, RamStats
    from .usage import UsageStats

# Panel sizing -- mirrors v1 constraints (lesson #13).
PANEL_WIDTH_DEFAULT = 38
PANEL_WIDTH_MIN = 24
PANEL_WIDTH_MAX = 60
PANEL_WIDTH_STEP = 2

UNASSIGNED_TEAM = "unassigned"

SORT_MODES: tuple[str, ...] = ("health", "alpha", "problem")
FILTER_MODES: tuple[str, ...] = (
    "all",
    "private",
    "public",
    "no-workspace",
    "broken-ci",
    "security",
    "no-renovate",
    "stale-prs",
    "issues",
)

_TEAM_FILTER_PREFIX = "team:"
_ORG_FILTER_PREFIX = "org:"
_TEAM_PERMISSION_ORDER = {"admin": 0, "maintain": 1, "push": 2, "triage": 3, "pull": 4}


@dataclass(frozen=True)
class RepoTeamInfo:
    """GitHub team ownership for a repo -- primary team plus every team."""

    primary: str = UNASSIGNED_TEAM
    all: tuple[str, ...] = ()


@dataclass(frozen=True)
class ErrorEntry:
    """A single entry in the error log drawer."""

    timestamp: datetime
    source: str  # "refresh" | "usage" | "cache" | "ui"
    message: str


@dataclass
class AppState:
    """Mutable app state. Widgets read from here; workers write to it."""

    healths: list[RepoHealth] = field(default_factory=list)
    health_by_name: dict[str, RepoHealth] = field(default_factory=dict)
    repo_teams: dict[str, RepoTeamInfo] = field(default_factory=dict)
    team_labels: dict[str, str] = field(default_factory=lambda: {UNASSIGNED_TEAM: "Unassigned"})
    usage_stats: list[UsageStats] = field(default_factory=list)
    gpu_stats: GpuStats | None = None
    ram_stats: RamStats | None = None
    errors: list[ErrorEntry] = field(default_factory=list)

    sort_mode: str = SORT_MODES[0]
    active_filters: set[str] = field(default_factory=set)
    layout_name: str = "packed"
    theme_name: str = "default"
    panel_width: int = PANEL_WIDTH_DEFAULT

    selected_full_name: str | None = None

    last_refresh_at: datetime | None = None
    next_refresh_at: datetime | None = None
    is_refreshing: bool = False
    consecutive_errors: int = 0
    last_error_message: str | None = None

    # Repos whose most recent refresh attempt failed (shown gray/stale).
    stale_repos: set[str] = field(default_factory=set)

    # Cooperative cancellation flag for threaded workers.
    cancel_requested: bool = False

    def log_error(self, source: str, message: str, *, limit: int = 200) -> None:
        self.errors.append(ErrorEntry(timestamp=datetime.now(UTC), source=source, message=message))
        if len(self.errors) > limit:
            del self.errors[: len(self.errors) - limit]

    def clear_errors(self) -> None:
        self.errors.clear()


# ---------------------------------------------------------------------------
# Team helpers
# ---------------------------------------------------------------------------


def team_filter_mode(team_key: str) -> str:
    return f"{_TEAM_FILTER_PREFIX}{team_key}"


def team_key_from_filter(mode: str) -> str | None:
    if mode.startswith(_TEAM_FILTER_PREFIX):
        return mode.removeprefix(_TEAM_FILTER_PREFIX)
    return None


# ---------------------------------------------------------------------------
# Org (owner) helpers
# ---------------------------------------------------------------------------


def org_filter_mode(owner: str) -> str:
    return f"{_ORG_FILTER_PREFIX}{owner}"


def org_key_from_filter(mode: str) -> str | None:
    if mode.startswith(_ORG_FILTER_PREFIX):
        return mode.removeprefix(_ORG_FILTER_PREFIX)
    return None


def owner_of(full_name: str) -> str:
    """Extract the owner (org or user) from a ``owner/repo`` full name."""
    return full_name.split("/", 1)[0] if "/" in full_name else full_name


def display_team_label(team_key: str, team_labels: dict[str, str]) -> str:
    if team_key == UNASSIGNED_TEAM:
        return "Unassigned"
    return team_labels.get(team_key, team_key.replace("-", " ").title())


# Golden-angle hue step -- guarantees that every new slot lands maximally far
# from the previously-used hues, so adjacent teams can never collide into
# similar colours (e.g. "woxom" vs "augmenting-integrations" both being blue).
_GOLDEN_ANGLE = 137.50776405003785
# Fixed saturation / lightness -- tuned for terminal legibility against both
# dark and paper-like backgrounds.
_TEAM_ACCENT_S = 0.60
_TEAM_ACCENT_L = 0.62


def _hsl_hex(hue_deg: float) -> str:
    h = (hue_deg % 360.0) / 360.0
    r, g, b = colorsys.hls_to_rgb(h, _TEAM_ACCENT_L, _TEAM_ACCENT_S)
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


def team_accent(team_key: str, known_teams: Iterable[str] | None = None) -> str:
    """Return a deterministic hex colour for ``team_key``.

    When ``known_teams`` is supplied, colours are assigned by *sorted index*
    through the golden-angle rotation, so every team in that set is guaranteed
    a maximally-distinct hue from its neighbours. Without it we fall back to
    hashing the team key into the same rotation -- still deterministic per
    key, but no longer guaranteed distinct between a given pair of keys.
    """
    if team_key == UNASSIGNED_TEAM:
        return "#808080"
    if known_teams is not None:
        keys = sorted({k for k in known_teams if k and k != UNASSIGNED_TEAM})
        if team_key in keys:
            idx = keys.index(team_key)
            return _hsl_hex(idx * _GOLDEN_ANGLE)
    digest = hashlib.sha256(team_key.encode("utf-8")).digest()
    idx = int.from_bytes(digest[:2], "big")
    return _hsl_hex(idx * _GOLDEN_ANGLE)


def _team_sort_key(team: object) -> tuple[int, str]:
    permission = getattr(team, "permission", "") or ""
    slug = getattr(team, "slug", "") or getattr(team, "name", "") or ""
    return _TEAM_PERMISSION_ORDER.get(permission, 99), slug.lower()


@dataclass(frozen=True)
class CollectedTeamData:
    """Thread-safe snapshot of team data for a single repo.

    Collected in worker threads, merged into ``AppState`` on the main
    thread to avoid concurrent dict mutation.
    """

    full_name: str
    info: RepoTeamInfo = RepoTeamInfo()
    labels: dict[str, str] = field(default_factory=dict)
    error: str | None = None


def collect_repo_teams(repo: Repository) -> CollectedTeamData:
    """Fetch team data for a repo without mutating shared state.

    Safe to call from worker threads.  Returns a ``CollectedTeamData``
    snapshot that the caller merges on the main thread via
    ``merge_team_data``.
    """
    full_name = getattr(repo, "full_name", "")
    if not full_name:
        return CollectedTeamData(full_name="")
    try:
        teams = sorted(repo.get_teams(), key=_team_sort_key)
    except GithubException as exc:
        # 403/404 are expected for personal (non-org) repos -- the Teams API
        # doesn't apply to user repos.  Treat as "no teams" without logging
        # a user-visible error that would flash the error drawer.
        if exc.status in (403, 404):
            logger.debug(
                f"collect_repo_teams: {full_name}: expected {exc.status} (not an org repo)"
            )
            return CollectedTeamData(full_name=full_name)
        logger.debug(f"collect_repo_teams: {full_name}: {exc.__class__.__name__}: {exc}")
        return CollectedTeamData(
            full_name=full_name,
            error=f"{full_name}: teams: {exc.__class__.__name__}: {exc}",
        )
    except Exception as exc:
        logger.debug(f"collect_repo_teams: {full_name}: {exc.__class__.__name__}: {exc}")
        return CollectedTeamData(
            full_name=full_name,
            error=f"{full_name}: teams: {exc.__class__.__name__}: {exc}",
        )

    team_keys: list[str] = []
    labels: dict[str, str] = {}
    for team in teams:
        slug = getattr(team, "slug", "") or ""
        if not slug:
            continue
        name = getattr(team, "name", "") or slug
        team_keys.append(slug)
        labels[slug] = name

    if not team_keys:
        return CollectedTeamData(full_name=full_name, labels=labels)
    return CollectedTeamData(
        full_name=full_name,
        info=RepoTeamInfo(primary=team_keys[0], all=tuple(team_keys)),
        labels=labels,
    )


def merge_team_data(state: AppState, collected: list[CollectedTeamData]) -> None:
    """Apply collected team snapshots to state. Must run on the main thread."""
    for td in collected:
        if not td.full_name:
            continue
        if td.error:
            # Keep previous data if we have it; otherwise mark unassigned.
            if td.full_name not in state.repo_teams:
                state.repo_teams[td.full_name] = RepoTeamInfo()
            continue
        state.repo_teams[td.full_name] = td.info
        state.team_labels.update(td.labels)


# ---------------------------------------------------------------------------
# Reducers (pure functions; easy to unit-test)
# ---------------------------------------------------------------------------


def apply_sort(healths: list[RepoHealth], mode: str) -> list[RepoHealth]:
    if mode == "alpha":
        return sorted(healths, key=lambda h: h.status.name.lower())
    if mode == "problem":
        return sorted(healths, key=lambda h: (int(h.worst_severity), h.status.name.lower()))
    return sorted(healths, key=lambda h: h.score)


def _matches_filter(h: RepoHealth, mode: str, repo_teams: dict[str, RepoTeamInfo]) -> bool:
    """Check whether a single health entry passes a single filter mode."""
    if mode == "private":
        return h.status.private
    if mode == "public":
        return not h.status.private
    if mode == "no-workspace":
        return not h.status.is_workspace
    if mode == "broken-ci":
        return any(c.check_name == "broken_ci" and c.severity != Severity.OK for c in h.checks)
    if mode == "security":
        return any(
            c.check_name == "security_alerts" and c.severity != Severity.OK for c in h.checks
        )
    if mode == "no-renovate":
        return any(
            c.check_name == "renovate_enabled" and c.severity != Severity.OK for c in h.checks
        )
    if mode == "stale-prs":
        return any(c.check_name == "stale_prs" and c.severity != Severity.OK for c in h.checks)
    if mode == "issues":
        return any(c.check_name == "open_issues" and c.severity != Severity.OK for c in h.checks)
    team_key = team_key_from_filter(mode)
    if team_key is not None:
        info = repo_teams.get(h.status.full_name)
        if info is None:
            # Team data not fetched yet -- include the repo so it isn't
            # hidden just because the refresh hasn't completed.
            return True
        if team_key == UNASSIGNED_TEAM:
            return info.primary == UNASSIGNED_TEAM
        return team_key in info.all
    org_key = org_key_from_filter(mode)
    if org_key is not None:
        return owner_of(h.status.full_name) == org_key
    return True


def apply_filter(
    healths: list[RepoHealth],
    mode: str,
    repo_teams: dict[str, RepoTeamInfo] | None = None,
) -> list[RepoHealth]:
    if mode == "all":
        return list(healths)
    teams = repo_teams or {}
    return [h for h in healths if _matches_filter(h, mode, teams)]


def apply_active_filters(
    healths: list[RepoHealth],
    active: set[str],
    repo_teams: dict[str, RepoTeamInfo] | None = None,
) -> list[RepoHealth]:
    """Apply multiple filters.

    ``no-workspace`` is the only hard AND constraint -- it always
    excludes workspace repos when checked (persistent toggle via ``w``).

    Every other filter is an OR **selection**: each checked item adds
    matching repos to the visible set.  This matches the mental model
    of a multi-select checklist where checking ``broken-ci`` +
    ``team:woxom`` means "show broken-CI repos PLUS woxom repos."
    """
    if not active:
        return list(healths)
    teams = repo_teams or {}
    # no-workspace is a hard exclusion (the 'w' toggle).
    has_no_workspace = "no-workspace" in active
    # Everything else ORs together as additive selections.
    selections = sorted(m for m in active if m != "no-workspace")
    result: list[RepoHealth] = []
    for h in healths:
        if has_no_workspace and not _matches_filter(h, "no-workspace", teams):
            continue
        if selections and not any(_matches_filter(h, mode, teams) for mode in selections):
            continue
        result.append(h)
    return result


def available_filter_modes(
    team_labels: dict[str, str],
    repo_teams: dict[str, RepoTeamInfo],
    healths: list[RepoHealth] | None = None,
) -> list[str]:
    team_keys = {team for info in repo_teams.values() for team in (info.all or (info.primary,))}
    team_dynamic = [
        team_filter_mode(team_key)
        for team_key in sorted(
            team_keys, key=lambda key: display_team_label(key, team_labels).lower()
        )
    ]
    # Org filters derived from the owners present in the current health data.
    org_keys: set[str] = set()
    if healths:
        org_keys = {owner_of(h.status.full_name) for h in healths}
    org_dynamic = [org_filter_mode(key) for key in sorted(org_keys)]
    return [*FILTER_MODES, *org_dynamic, *team_dynamic]


def visible_healths(state: AppState) -> list[RepoHealth]:
    return apply_sort(
        apply_active_filters(state.healths, state.active_filters, state.repo_teams),
        state.sort_mode,
    )


def ensure_selection(state: AppState) -> None:
    vis = visible_healths(state)
    if not vis:
        state.selected_full_name = None
        return
    names = {h.status.full_name for h in vis}
    if state.selected_full_name not in names:
        state.selected_full_name = vis[0].status.full_name


def selected_health(state: AppState) -> RepoHealth | None:
    ensure_selection(state)
    if state.selected_full_name is None:
        return None
    return state.health_by_name.get(state.selected_full_name)


def move_selection(state: AppState, delta: int) -> None:
    vis = visible_healths(state)
    if not vis:
        return
    ensure_selection(state)
    current_index = next(
        (i for i, h in enumerate(vis) if h.status.full_name == state.selected_full_name),
        0,
    )
    new_index = max(0, min(len(vis) - 1, current_index + delta))
    state.selected_full_name = vis[new_index].status.full_name


# ---------------------------------------------------------------------------
# Cache bootstrap
# ---------------------------------------------------------------------------


def bootstrap_from_cache(state: AppState, restrict_to: set[str] | None = None) -> bool:
    """Populate state.healths from the on-disk cache. Returns True if loaded."""
    try:
        cache = load_cache()
        if restrict_to is not None:
            cache = {k: v for k, v in cache.items() if k in restrict_to}
        if not cache:
            return False
        statuses: list[RepoStatus] = list(cache.values())
        health_cache = load_health_cache(cache)
        state.healths = [health_cache.get(s.full_name) or RepoHealth(status=s) for s in statuses]
        state.health_by_name = {h.status.full_name: h for h in state.healths}
        return True
    except Exception as exc:
        state.log_error("cache", f"{exc.__class__.__name__}: {exc}")
        return False
