"""WidgetHelpScreen -- modal explaining what a top-drawer widget means.

Clicking any section of the org drawer (the ``i``-toggled top drawer) opens
this screen with a short, plain-English explanation of the chosen widget.
Each widget is identified by a stable id (e.g. ``activity`` or ``ci_matrix``)
that maps to a ``(title, body)`` pair in :data:`WIDGET_HELP`.
"""

from __future__ import annotations

from rich.text import Text
from textual import events
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Static

# Section ids used by TopDrawer. Keep in sync with the ids assigned in
# augint_tools.dashboard.app.MainScreen._org_drawer_*_sections().
WIDGET_HELP: dict[str, tuple[str, str]] = {
    "system": (
        "system",
        (
            "Host machine meters for the computer running the dashboard, not the "
            "GitHub org. RAM shows used/total memory with a severity-coloured bar; "
            "GPU (when an NVIDIA card is present) shows name, temperature, power, "
            "utilisation, and VRAM. If neither probe has data, this section is hidden."
        ),
    ),
    "ci_matrix": (
        "ci matrix",
        (
            "One row per repo (up to 24) showing CI status as coloured dots. For "
            "service repos the left dot is the dev-branch workflow and the right dot "
            "is main; library repos only show the main dot. Green = passing, red = "
            "failing, yellow = in progress, grey = unknown. Any overflow is "
            'summarised as "(+N more)".'
        ),
    ),
    "severity_bar": (
        "severity bar",
        (
            "Horizontal stacked bar showing what fraction of repos sit in each "
            "severity bucket: critical, high, medium, low, and ok. The legend on "
            "the right lists every non-zero bucket with its count; when nothing "
            "is wrong it falls back to an aggregated view of open issues and "
            "PRs across the workspace, each clickable through to GitHub so you "
            "can pick up work directly from here."
        ),
    ),
    "repo_glyphs": (
        "repo glyphs",
        (
            "One coloured dot per repo (up to 50), coloured by that repo's worst "
            "current severity. Scan left-to-right for red or orange pips to spot "
            "which repos are dragging the org down. Overflow past 50 is shown as "
            '"(+N more)".'
        ),
    ),
    "weather": (
        "weather",
        (
            'Single-glance forecast of org health. "Sunny" means every repo is '
            'green; "partly cloudy" means only low/medium findings; "overcast" '
            "means there is at least one high-severity repo or a failing main CI; "
            '"stormy" means critical findings or two-plus CI failures. The tail '
            "lists the worst contributing counts."
        ),
    ),
    "activity": (
        "activity",
        (
            "Seven-day sparkline of your local Claude Code message volume, one bar "
            "per day. Taller bars mean busier days. The suffix shows the 7-day "
            "total and the peak day's count; if no Claude usage data is available "
            'it reads "no recent Claude activity".'
        ),
    ),
    "pr_ages": (
        "pr ages",
        (
            "Composite bar summarising the org's open PRs. The green segment is "
            '"active" PRs, the red segment is "stale" PRs (as flagged by the '
            "stale_prs health check), and the dim segment is drafts. Numbers on "
            "the right are active / stale-count s / drafts-count d."
        ),
    ),
    "team_mix": (
        "team mix",
        (
            "Breakdown of repos by primary team ownership. The bar is segmented "
            "and coloured per team in proportion to how many repos each team owns; "
            "the legend underneath lists the top four teams with counts and "
            'overflow as "+N".'
        ),
    ),
    "usage": (
        "usage",
        (
            "Your AI tool usage against each configured provider's plan limits: "
            "Claude Code, OpenAI, GitHub Copilot. Each meter shows a progress bar "
            "coloured by how close you are to the cap, plus tier and percentage. "
            "Providers that are not configured are hidden."
        ),
    ),
    "check_breakdown": (
        "failing checks",
        (
            "Tally of health-check failures across the whole org, sorted by how "
            "many repos each check is failing on (top six). The coloured bar is "
            "proportional to the failing-check count and coloured by the worst "
            "severity seen for that check. Use this to spot which check is the "
            "biggest source of pain."
        ),
    ),
    "service_lib": (
        "service / lib",
        (
            "Split of repos into services versus libraries, with a green/red "
            'tally of how many are fully green ("ok") versus have at least one '
            'non-ok finding ("err"). A quick sanity check on whether failures '
            "cluster on one side or the other."
        ),
    ),
    "score_histogram": (
        "scores",
        (
            "Distribution of repo health scores in ten 10-point buckets from 0 to "
            "100. Taller glyphs mean more repos in that bucket; the left half "
            "(below 50) is coloured red to make low-scoring clusters obvious. "
            'The "avg" line underneath is the mean display score across all '
            "repos."
        ),
    ),
    "recent_errors": (
        "recent errors",
        (
            "The last three errors the dashboard itself captured -- GitHub API "
            "failures, token problems, rate limits, etc. Each line shows the "
            "time, source, and truncated message. If the list is empty the "
            "dashboard has not hit any errors this session."
        ),
    ),
    "leaderboard": (
        "worst 5",
        (
            "The five unhealthiest repos, ranked by score (lower is worse) then "
            "severity. Each line shows the rank, repo name, display score "
            "coloured by severity, and the first non-ok finding's summary. Hidden "
            "entirely when every repo is green."
        ),
    ),
}


class WidgetHelpScreen(ModalScreen[None]):
    """Modal popup that explains what a single top-drawer widget means."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("question_mark", "dismiss", "Close"),
    ]

    def __init__(self, widget_id: str) -> None:
        super().__init__()
        self._widget_id = widget_id
        self._title, self._body = WIDGET_HELP.get(
            widget_id,
            (
                widget_id,
                "No explanation is available for this widget yet.",
            ),
        )

    def compose(self):
        text = Text()
        text.append(f"{self._title}\n\n", style="bold")
        text.append(self._body)
        text.append("\n\npress esc or click outside to close.", style="dim")
        body = Static(text, id="widget-help-body")
        yield Container(body, id="help-body")

    @property
    def widget_id(self) -> str:
        return self._widget_id

    @property
    def title_text(self) -> str:
        return self._title

    @property
    def body_text(self) -> str:
        return self._body

    def action_dismiss(self, result: None = None) -> None:  # type: ignore[override]
        self.dismiss()

    def on_click(self, event: events.Click) -> None:
        """Right-click anywhere dismisses, matching other modal screens."""
        if event.button == 3:
            self.dismiss()
