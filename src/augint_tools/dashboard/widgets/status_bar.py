"""StatusBar -- org, sort/filter/layout/theme, error chip.

The refresh countdown lives on the OrgDrawerHeader (subtle, grey, right-aligned)
rather than here, so this bar stays focused on structural context.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual.widgets import Static

if TYPE_CHECKING:
    from ..state import AppState


_FILTER_LABELS: dict[str, str] = {
    "all": "all repos",
    "broken-ci": "broken CI",
    "no-renovate": "no renovate",
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


class StatusBar(Static):
    """Single-line status bar docked at the top of the main screen."""

    DEFAULT_CSS = """
    StatusBar { height: 1; }
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
            self.update("loading…")
            return
        t = Text()
        t.append(f" {self._org_name or 'no-org'} ", style="bold")
        filter_label = describe_filter(state.filter_mode, state.team_labels)
        t.append(
            f"| sort: {state.sort_mode} "
            f"| filter: {filter_label} "
            f"| layout: {state.layout_name} "
            f"| theme: {state.theme_name}"
        )
        error_chip = self._error_chip(state)
        if error_chip:
            t.append(f" | {error_chip}", style="bold red")
        self.update(t)

    def _error_chip(self, state: AppState) -> str:
        count = len(state.errors)
        if count == 0:
            return ""
        return f"[{count} errors]"
