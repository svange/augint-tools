"""Central state and pure reducers for the v2 dashboard."""

from __future__ import annotations

import colorsys
import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

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
    "no-workspace",
    "broken-ci",
    "security",
    "no-renovate",
    "stale-prs",
    "issues",
)

_TEAM_FILTER_PREFIX = "team:"
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


def remember_repo_teams(state: AppState, repo: Repository) -> None:
    """Populate state.repo_teams for a repo. Safe on API failure."""
    full_name = getattr(repo, "full_name", "")
    if not full_name or full_name in state.repo_teams:
        return
    try:
        teams = sorted(repo.get_teams(), key=_team_sort_key)
    except Exception:
        state.repo_teams[full_name] = RepoTeamInfo()
        return

    team_keys: list[str] = []
    for team in teams:
        slug = getattr(team, "slug", "") or ""
        if not slug:
            continue
        name = getattr(team, "name", "") or slug
        team_keys.append(slug)
        state.team_labels.setdefault(slug, name)

    if not team_keys:
        state.repo_teams[full_name] = RepoTeamInfo()
        return
    state.repo_teams[full_name] = RepoTeamInfo(primary=team_keys[0], all=tuple(team_keys))


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
        if team_key == UNASSIGNED_TEAM:
            return repo_teams.get(h.status.full_name, RepoTeamInfo()).primary == UNASSIGNED_TEAM
        return team_key in repo_teams.get(h.status.full_name, RepoTeamInfo()).all
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
    """Apply multiple filters. Non-team filters AND together; team filters OR together."""
    if not active:
        return list(healths)
    teams = repo_teams or {}
    non_team = sorted(m for m in active if not m.startswith(_TEAM_FILTER_PREFIX))
    team_modes = sorted(m for m in active if m.startswith(_TEAM_FILTER_PREFIX))
    result: list[RepoHealth] = []
    for h in healths:
        if not all(_matches_filter(h, mode, teams) for mode in non_team):
            continue
        if team_modes and not any(_matches_filter(h, mode, teams) for mode in team_modes):
            continue
        result.append(h)
    return result


def available_filter_modes(
    team_labels: dict[str, str], repo_teams: dict[str, RepoTeamInfo]
) -> list[str]:
    team_keys = {team for info in repo_teams.values() for team in (info.all or (info.primary,))}
    dynamic = [
        team_filter_mode(team_key)
        for team_key in sorted(
            team_keys, key=lambda key: display_team_label(key, team_labels).lower()
        )
    ]
    return [*FILTER_MODES, *dynamic]


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
