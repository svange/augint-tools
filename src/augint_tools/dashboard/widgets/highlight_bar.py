"""HighlightBar -- worst repo, stale PR, totals, compact usage meters."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual.widgets import Static

from ..health import Severity
from ..usage import UsageStats

if TYPE_CHECKING:
    from ..state import AppState


def _fmt_countdown(seconds: int) -> str:
    """Format remaining seconds as compact 'Xh Ym' / 'Xm' / 'Xs'."""
    if seconds <= 0:
        return "now"
    if seconds >= 3600:
        h, rem = divmod(seconds, 3600)
        m = rem // 60
        return f"{h}h{m:02d}m" if m else f"{h}h"
    if seconds >= 60:
        m, s = divmod(seconds, 60)
        return f"{m}m"
    return f"{seconds}s"


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

        line2 = self._usage_line(state)
        self.update(Text("\n").join([line1, line2, Text(" ")]))

    def _usage_line(self, state: AppState) -> Text:
        t = Text()
        if not state.usage_stats:
            t.append("usage: ", style="bold")
            t.append("loading...", style="dim")
            return t
        t.append("usage: ", style="bold")
        first = True
        for stats in state.usage_stats:
            if not first:
                t.append("  |  ", style="dim")
            first = False
            if stats.provider == "claude_code":
                self._append_claude_usage(t, stats)
            else:
                t.append(f"{stats.display_name} ", style="bold")
                usage = stats.usage_fraction
                if usage is None:
                    t.append(stats.status, style="dim")
                else:
                    t.append(f"{int(usage * 100)}%")
        return t

    def _append_claude_usage(self, t: Text, stats: UsageStats) -> None:
        """Render Claude with both 5h and 7d windows plus reset countdown."""
        t.append("Claude ", style="bold")
        # 5-hour window
        h5 = stats.hour5_fraction
        if h5 is not None:
            color = "red" if h5 >= 0.9 else ("yellow" if h5 >= 0.7 else "")
            t.append("5h:", style="dim")
            t.append(f"{int(h5 * 100)}%", style=color)
        else:
            t.append("5h:", style="dim")
            t.append("--", style="dim")
        t.append(" ")
        # 7-day window
        w7 = stats.week7_fraction
        if w7 is not None:
            color = "red" if w7 >= 0.9 else ("yellow" if w7 >= 0.7 else "")
            t.append("7d:", style="dim")
            t.append(f"{int(w7 * 100)}%", style=color)
        else:
            t.append("7d:", style="dim")
            t.append("--", style="dim")
        # Reset countdown
        if stats.time_remaining_seconds is not None:
            t.append(f" resets {_fmt_countdown(stats.time_remaining_seconds)}", style="dim")
