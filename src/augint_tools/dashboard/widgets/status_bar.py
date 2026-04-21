"""StatusBar -- org, sort/filter/layout/theme, staleness + countdown + errors.

The staleness indicator ("loading" or "updated Xs ago") and the countdown
to the next refresh live here so they are always visible, even when the
org drawer is closed. The OrgDrawerHeader also renders a dim copy for
users whose eye goes to the top of the screen, but this bar is the
canonical source of truth.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from rich.text import Text
from textual.widgets import Static

if TYPE_CHECKING:
    from ..state import AppState


_FILTER_LABELS: dict[str, str] = {
    "all": "all repos",
    "private": "Private",
    "public": "Public (Open source)",
    "broken-ci": "Broken CI",
    "no-renovate": "No Renovate config",
    "renovate-prs-piling": "Renovate PRs piling up",
    "stale-prs": "Stale PRs",
    "issues": "Open issues",
}


def describe_filter(mode: str, team_labels: dict[str, str] | None = None) -> str:
    """Return a short human label for a filter mode (includes dynamic team filters)."""
    if mode in _FILTER_LABELS:
        return _FILTER_LABELS[mode]
    if mode.startswith("team:"):
        key = mode.removeprefix("team:")
        if team_labels and key in team_labels:
            return f"team: {team_labels[key]}"
        return f"team: {key}"
    if mode.startswith("org:"):
        return f"org: {mode.removeprefix('org:')}"
    return mode


def _format_age(seconds: int) -> str:
    """Format a timedelta in seconds as a compact 'Xs' / 'Xm' / 'Xh' string."""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        h, m = divmod(seconds, 3600)
        return f"{h}h{m // 60:02d}m" if m >= 60 else f"{h}h"
    d, rem = divmod(seconds, 86400)
    return f"{d}d{rem // 3600}h" if rem >= 3600 else f"{d}d"


def _format_countdown(seconds: int) -> str:
    """Format a remaining-time countdown as 'Xs' or 'MmSSs'."""
    if seconds < 0:
        seconds = 0
    if seconds >= 60:
        m, s = divmod(seconds, 60)
        return f"{m}m{s:02d}s"
    return f"{seconds}s"


def format_header_refresh_text(
    *,
    is_refreshing: bool,
    last_refresh_age_seconds: int | None,
    next_refresh_remaining_seconds: int | None,
) -> str:
    """Build the right-aligned header refresh indicator.

    Shows both halves -- "last: Xs ago" and "next: in Ys" -- so users can
    see at a glance both how fresh the on-screen data is and when the
    next auto-refresh will fire.

    Special states (first-launch loading, mid-refresh, auto-refresh
    disabled) collapse to a single phrase rather than padding the line
    with stale or meaningless values.

    Inputs are pre-computed integers so this function can be unit-tested
    without freezing time.
    """
    if is_refreshing and last_refresh_age_seconds is None:
        return "loading data..."
    if is_refreshing:
        # Mid-refresh with prior data: keep showing how stale the visible
        # data is; suppress the "next" half because it's about to reset.
        if last_refresh_age_seconds is not None:
            return f"last: {_format_age(max(0, last_refresh_age_seconds))} ago · refreshing..."
        return "refreshing..."
    if next_refresh_remaining_seconds is None:
        if last_refresh_age_seconds is not None:
            return f"last: {_format_age(max(0, last_refresh_age_seconds))} ago · auto-refresh off"
        return "auto-refresh off"
    last_part = (
        f"last: {_format_age(max(0, last_refresh_age_seconds))} ago"
        if last_refresh_age_seconds is not None
        else "last: --"
    )
    remaining = max(0, next_refresh_remaining_seconds)
    next_part = "next: now" if remaining == 0 else f"next: in {_format_countdown(remaining)}"
    return f"{last_part} · {next_part}"


def _format_interval(seconds: int) -> str:
    """Format the auto-refresh interval as "Xs", "Xm", or "XhYYm"."""
    if seconds <= 0:
        return "off"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m}m{s:02d}s" if s else f"{m}m"
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    return f"{h}h{m:02d}m" if m else f"{h}h"


def format_header_refresh_line(
    *,
    is_refreshing: bool,
    last_refresh_label: str | None,
    next_refresh_remaining_seconds: int | None,
    interval_seconds: int,
) -> str:
    """Build the prominent two-line-header second row.

    Unlike ``format_header_refresh_text`` (the narrow right-aligned chip),
    this line always names all three facts the user cares about:
    when the last refresh completed, when the next one fires, and the
    configured auto-refresh interval. The interval in particular is
    otherwise invisible to the user and easy to forget.

    Inputs are pre-computed so this can be unit-tested without freezing
    time. ``last_refresh_label`` is the caller's choice of timestamp
    format (typically local-time "HH:MM:SS").
    """
    interval_label = _format_interval(interval_seconds)
    interval_part = f"interval: {interval_label}"
    if is_refreshing and last_refresh_label is None:
        return f"loading data...  ·  {interval_part}"
    last_part = f"last refresh: {last_refresh_label}" if last_refresh_label else "last refresh: --"
    if is_refreshing:
        return f"{last_part}  ·  refreshing...  ·  {interval_part}"
    if next_refresh_remaining_seconds is None:
        return f"{last_part}  ·  auto-refresh off"
    remaining = max(0, next_refresh_remaining_seconds)
    next_label = "now" if remaining == 0 else _format_countdown(remaining)
    return f"{last_part}  ·  next: in {next_label}  ·  {interval_part}"


def _describe_active_filters(active: set[str], team_labels: dict[str, str] | None = None) -> str:
    """Summarise the active filter set for the status bar."""
    if not active:
        return "all repos"
    labels = [describe_filter(m, team_labels) for m in sorted(active)]
    joined = " + ".join(labels)
    if len(joined) <= 40:
        return joined
    return f"{len(active)} filters"


class StatusBar(Static):
    """Single-line status bar docked at the top of the main screen."""

    DEFAULT_CSS = """
    StatusBar { height: 1; overflow: hidden; }
    """

    def __init__(self, id: str = "status-bar") -> None:
        super().__init__("", id=id)
        self._state: AppState | None = None
        self._org_name: str = ""

    def bind_state(
        self, state: AppState, org_name: str = "", owners: list[str] | None = None
    ) -> None:
        self._state = state
        self._org_name = org_name
        self._owners: list[str] = owners or ([org_name] if org_name else [])

    def tick(self) -> None:
        """Re-render. Call every second from the app."""
        self._rerender()

    def _rerender(self) -> None:
        state = self._state
        if state is None:
            self.update("loading...")
            return
        t = Text()
        owner_label = " + ".join(self._owners) if self._owners else self._org_name or "no-org"
        t.append(f" {owner_label} ", style="bold")
        # Refresh / staleness first -- most important at a glance.
        stale_chip = self._staleness_chip(state)
        if stale_chip:
            t.append("| ")
            t.append(stale_chip[0], style=stale_chip[1])
            t.append(" ")
        refresh_chip = self._refresh_chip(state)
        if refresh_chip:
            t.append("| ")
            t.append(refresh_chip[0], style=refresh_chip[1])
            t.append(" ")
        error_chip = self._error_chip(state)
        if error_chip:
            t.append(f"| {error_chip} ", style="bold red")
        if state.hide_workspace:
            t.append("| ws:hidden ", style="bold magenta")
        filter_label = _describe_active_filters(state.active_filters, state.team_labels)
        t.append(
            f"| sort: {state.sort_mode} "
            f"| filter: {filter_label} "
            f"| layout: {state.layout_name} "
            f"| theme: {state.theme_name}"
        )
        self.update(t)

    def _staleness_chip(self, state: AppState) -> tuple[str, str] | None:
        """Return ("loading..." | "updated Xs ago", style) to show data freshness.

        Shown at all times so users see a clear "yes we're working"
        indicator during the 20-30s first-fetch window, and always know
        roughly how fresh the on-screen data is between refreshes.
        """
        if state.is_refreshing and state.last_refresh_at is None:
            return ("loading data...", "bold yellow")
        if state.last_refresh_at is None:
            # No cache and no refresh running -- offline / skipped refresh.
            return ("no data yet", "bold red")
        age = int((datetime.now(UTC) - state.last_refresh_at).total_seconds())
        if age < 0:
            age = 0
        label = _format_age(age)
        # Green while fresh (<=60s), yellow mid (<=10m), dim otherwise.
        if age <= 60:
            style = "green"
        elif age <= 600:
            style = "yellow"
        else:
            style = "dim"
        return (f"updated {label} ago", style)

    def _refresh_chip(self, state: AppState) -> tuple[str, str] | None:
        if state.is_refreshing and state.last_refresh_at is not None:
            # During a refresh that has prior data, staleness chip shows age;
            # this chip announces that a refresh is in progress.
            return ("refreshing...", "bold yellow")
        if state.next_refresh_at is not None and not state.is_refreshing:
            remaining = int((state.next_refresh_at - datetime.now(UTC)).total_seconds())
            if remaining <= 0:
                return ("next: now", "bold cyan")
            if remaining >= 60:
                m, s = divmod(remaining, 60)
                return (f"next: {m}m{s:02d}s", "cyan")
            return (f"next: {remaining}s", "cyan")
        return None

    def _error_chip(self, state: AppState) -> str:
        count = len(state.errors)
        if count == 0:
            return ""
        return f"[{count} errors]"
