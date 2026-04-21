"""HighlightBar -- worst repo, stale PR, totals summary."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from rich.text import Text
from textual.widgets import Static

from ..health import Severity
from .status_bar import format_header_refresh_line

if TYPE_CHECKING:
    from ..state import AppState


class HighlightBar(Static):
    """Three-line at-a-glance summary bar.

    Line 1 is the worst-repo / totals summary. Line 2 is the prominent
    refresh status (time of last refresh, live countdown, interval) --
    the header above the status bar is only a single row so the refresh
    line needs to live here to be actually visible at a glance.
    Line 3 is a spacer that the status bar docks into the gap above.
    """

    DEFAULT_CSS = """
    HighlightBar { height: 3; }
    """

    def __init__(self, id: str = "highlight-bar") -> None:
        super().__init__("", id=id)
        self._state: AppState | None = None
        self._refresh_seconds: int = 0

    def bind_state(self, state: AppState, *, refresh_seconds: int = 0) -> None:
        self._state = state
        self._refresh_seconds = refresh_seconds

    def rerender(self) -> None:
        state = self._state
        if state is None or not state.healths:
            if state is not None and state.is_refreshing:
                top = Text("loading repository data...", style="bold yellow")
            else:
                top = Text("no data yet. press r to refresh.", style="dim")
            self.update(Text("\n").join([top, self._refresh_line(), Text(" ")]))
            return

        worst = min(state.healths, key=lambda h: h.score)
        problems = [h for h in state.healths if h.worst_severity != Severity.OK]
        total_issues = sum(h.status.open_issues for h in state.healths)
        total_prs = sum(h.status.open_prs for h in state.healths)

        line1 = Text()
        line1.append("worst: ", style="bold")
        line1.append(f"{worst.status.name} ({worst.worst_severity.name.lower()})")
        line1.append("   problems: ", style="bold")
        line1.append(str(len(problems)))
        line1.append("   issues: ", style="bold")
        line1.append(str(total_issues))
        line1.append("   prs: ", style="bold")
        line1.append(str(total_prs))

        self.update(Text("\n").join([line1, self._refresh_line(), Text(" ")]))

    def _refresh_line(self) -> Text:
        """Build the bold second-line refresh status.

        Styled bold cyan so the user's eye lands on it -- the whole point
        is that "refreshes are happening" must be unmissable.
        """
        state = self._state
        if state is None:
            return Text(" ")
        now = datetime.now(UTC)
        last_label = (
            state.last_refresh_at.astimezone().strftime("%H:%M:%S")
            if state.last_refresh_at is not None
            else None
        )
        remaining = (
            int((state.next_refresh_at - now).total_seconds())
            if state.next_refresh_at is not None
            else None
        )
        text = format_header_refresh_line(
            is_refreshing=state.is_refreshing,
            last_refresh_label=last_label,
            next_refresh_remaining_seconds=remaining,
            interval_seconds=self._refresh_seconds,
        )
        return Text(text, style="bold cyan")
