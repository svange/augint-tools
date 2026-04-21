"""HighlightBar -- worst repo, stale PR, totals summary."""

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
            if state is not None and state.is_refreshing:
                self.update(Text("loading repository data...", style="bold yellow"))
            else:
                self.update(Text("no data yet. press r to refresh.", style="dim"))
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

        self.update(Text("\n").join([line1, Text(" "), Text(" ")]))
