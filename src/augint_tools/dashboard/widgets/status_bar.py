"""StatusBar -- org, sort/filter/layout/theme, error chip.

The refresh countdown lives on the OrgDrawerHeader (subtle, grey, right-aligned)
rather than here, so this bar stays focused on structural context.
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
    "broken-ci": "broken CI",
    "security": "security alerts",
    "no-renovate": "no renovate",
    "no-workspace": "no workspace",
    "stale-prs": "stale PRs",
    "issues": "has issues",
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
    return mode


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

    def bind_state(self, state: AppState, org_name: str) -> None:
        self._state = state
        self._org_name = org_name

    def tick(self) -> None:
        """Re-render. Call every second from the app."""
        self._rerender()

    def _rerender(self) -> None:
        state = self._state
        if state is None:
            self.update("loading...")
            return
        t = Text()
        t.append(f" {self._org_name or 'no-org'} ", style="bold")
        filter_label = _describe_active_filters(state.active_filters, state.team_labels)
        t.append(
            f"| sort: {state.sort_mode} "
            f"| filter: {filter_label} "
            f"| layout: {state.layout_name} "
            f"| theme: {state.theme_name}"
        )
        refresh_chip = self._refresh_chip(state)
        if refresh_chip:
            t.append(f" | {refresh_chip[0]}", style=refresh_chip[1])
        error_chip = self._error_chip(state)
        if error_chip:
            t.append(f" | {error_chip}", style="bold red")
        self.update(t)

    def _refresh_chip(self, state: AppState) -> tuple[str, str] | None:
        if state.is_refreshing:
            return ("refreshing...", "bold yellow")
        if state.next_refresh_at is not None:
            remaining = int((state.next_refresh_at - datetime.now(UTC)).total_seconds())
            if remaining <= 0:
                return ("next: now", "dim")
            if remaining >= 60:
                m, s = divmod(remaining, 60)
                return (f"next: {m}m{s:02d}s", "dim")
            return (f"next: {remaining}s", "dim")
        return None

    def _error_chip(self, state: AppState) -> str:
        count = len(state.errors)
        if count == 0:
            return ""
        return f"[{count} errors]"
