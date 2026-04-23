"""HighlightBar -- worst repo, stale PR, totals summary."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from rich.text import Text
from textual.widgets import Static

from ..health import Severity
from ..state import owner_of, visible_healths
from .status_bar import _format_age, _format_countdown

if TYPE_CHECKING:
    from ..state import AppState


class HighlightBar(Static):
    """Three-line at-a-glance summary bar.

    Line 1: worst-repo + totals + per-org health fractions.
    Line 2: refresh status + GitHub rate limits + network ping.
    Line 3: spacer that the status bar docks into.
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
            self.update(Text("\n").join([top, self._infra_line(), Text(" ")]))
            return

        worst = min(state.healths, key=lambda h: h.score)
        visible = visible_healths(state)
        total_problems = sum(1 for h in state.healths if h.worst_severity != Severity.OK)
        total_issues = sum(h.status.open_issues for h in state.healths)
        total_prs = sum(h.status.open_prs for h in state.healths)
        visible_problems = sum(1 for h in visible if h.worst_severity != Severity.OK)
        visible_issues = sum(h.status.open_issues for h in visible)
        visible_prs = sum(h.status.open_prs for h in visible)

        line1 = Text()
        line1.append("worst: ", style="bold")
        line1.append(f"{worst.status.name} ({worst.worst_severity.name.lower()})")
        line1.append("   problems: ", style="bold")
        line1.append(f"{visible_problems}/{total_problems}")
        line1.append("   issues: ", style="bold")
        line1.append(f"{visible_issues}/{total_issues}")
        line1.append("   prs: ", style="bold")
        line1.append(f"{visible_prs}/{total_prs}")

        # Per-org health score fractions.
        org_passed: dict[str, int] = defaultdict(int)
        org_total: dict[str, int] = defaultdict(int)
        for h in state.healths:
            org = owner_of(h.status.full_name)
            org_passed[org] += h.passed_checks
            org_total[org] += h.total_checks
        for owner in sorted(org_passed):
            p, t = org_passed[owner], org_total[owner]
            line1.append(f"   {owner}: ", style="bold")
            ratio = p / t if t > 0 else 1.0
            color = "green" if ratio >= 1.0 else "yellow" if ratio > 0.5 else "red"
            line1.append(f"{p}/{t}", style=color)

        self.update(Text("\n").join([line1, self._infra_line(), Text(" ")]))

    def _infra_line(self) -> Text:
        """Build the second-line infrastructure status.

        Combines refresh countdown, GitHub API rate limits (with explicit
        "resets" direction), and network ping on a single compact line.
        Styled bold cyan so the user's eye lands on it.
        """
        state = self._state
        if state is None:
            return Text(" ")

        line = Text()
        now = datetime.now(UTC)

        # -- refresh section --
        self._append_refresh(line, state, now)

        # -- rate limits section --
        line.append("  ", style="dim")
        self._append_rate_limits(line, state, now)

        # -- network ping section --
        self._append_ping(line, state)

        return line

    def _append_refresh(self, line: Text, state: AppState, now: datetime) -> None:
        """Append compact refresh status: last / next / interval."""
        if state.is_refreshing and state.last_refresh_at is None:
            line.append("loading...", style="bold yellow")
            return

        if state.last_refresh_at is not None:
            age = max(0, int((now - state.last_refresh_at).total_seconds()))
            line.append(f"last: {_format_age(age)} ago", style="bold cyan")
        else:
            line.append("last: --", style="bold cyan")

        if state.is_refreshing:
            line.append("  refreshing...", style="bold yellow")
            return

        if state.next_refresh_at is not None:
            remaining = max(0, int((state.next_refresh_at - now).total_seconds()))
            next_label = "now" if remaining == 0 else _format_countdown(remaining)
            line.append(f"  next: {next_label}", style="bold cyan")

        if self._refresh_seconds > 0:
            m, s = divmod(self._refresh_seconds, 60)
            iv = f"{m}m" if s == 0 else f"{m}m{s:02d}s" if m else f"{s}s"
            line.append(f"  ({iv})", style="dim cyan")

    def _append_rate_limits(self, line: Text, state: AppState, now: datetime) -> None:
        """Append GQL + REST rate limits with explicit 'resets' direction."""
        for label, remaining, limit, reset_at in [
            ("GQL", state.gql_remaining, state.gql_limit, state.gql_reset_at),
            ("REST", state.rest_remaining, state.rest_limit, state.rest_reset_at),
        ]:
            used = limit - remaining
            ratio = remaining / limit if limit > 0 else 1.0
            color = "green" if ratio > 0.5 else "yellow" if ratio > 0.2 else "red"
            line.append(f"{label}: ", style="bold")
            line.append(f"{used}/{limit}", style=color)
            if reset_at is not None:
                secs = max(0, int((reset_at - now).total_seconds()))
                mins, s = divmod(secs, 60)
                reset_label = f"{mins}m{s:02d}s" if mins else f"{s}s"
                line.append(f" resets {reset_label}", style="dim")
            line.append("  ")

    def _append_ping(self, line: Text, state: AppState) -> None:
        """Append network ping latency or offline indicator."""
        ping = state.ping_result
        if ping is None:
            return
        if not ping.connected:
            dns_fail = sum(1 for d in state.dns_results if not d.resolved)
            label = "OFFLINE"
            if dns_fail:
                label += f" ({dns_fail} DNS)"
            line.append(label, style="bold red")
            return
        if ping.latency_ms is not None:
            latency = ping.latency_ms
            style = "green" if latency < 50 else "yellow" if latency < 150 else "red"
            line.append(f"ping: {latency:.0f}ms", style=style)
