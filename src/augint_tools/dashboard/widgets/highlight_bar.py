"""HighlightBar -- worst repo, stale PR, totals, compact usage meters."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual.widgets import Static

from ..health import Severity

if TYPE_CHECKING:
    from ..state import AppState


class HighlightBar(Static):
    """Three-line at-a-glance summary bar."""

    DEFAULT_CSS = """
    HighlightBar { height: 3; }
    """

    def __init__(self, id: str = "highlight-bar") -> None:
        super().__init__("", id=id)
        self._state: AppState | None = None

    def bind_state(self, state: AppState) -> None:
        self._state = state

    def rerender(self) -> None:
        state = self._state
        if state is None or not state.healths:
            self.update(Text("no data yet", style="dim"))
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

        line2 = self._usage_line(state)
        self.update(Text("\n").join([line1, line2, Text(" ")]))

    def _usage_line(self, state: AppState) -> Text:
        t = Text()
        if not state.usage_stats:
            t.append("usage: ", style="bold")
            t.append("loading…", style="dim")
            return t
        t.append("usage: ", style="bold")
        first = True
        for stats in state.usage_stats:
            if not first:
                t.append("  ·  ", style="dim")
            first = False
            t.append(f"{stats.display_name} ", style="bold")
            usage = stats.usage_fraction
            if usage is None:
                t.append(stats.status, style="dim")
            else:
                t.append(f"{int(usage * 100)}%")
        return t
