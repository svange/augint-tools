"""DrillDownScreen -- full detail for a single repo."""

from __future__ import annotations

import webbrowser
from typing import TYPE_CHECKING

from rich.text import Text
from textual import events
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Static

from ..health import Severity

if TYPE_CHECKING:
    from ..health import RepoHealth


class DrillDownScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("o", "open_browser", "Open on GitHub"),
        Binding("enter", "open_browser", "Open on GitHub"),
    ]

    def __init__(self, health: RepoHealth) -> None:
        super().__init__()
        self._health = health

    def compose(self):
        body = Static(self._build_body(), id="drilldown-body")
        yield Container(body, id="help-body")

    def _build_body(self) -> Text:
        health = self._health
        status = health.status
        t = Text()
        t.append(f"{status.full_name}\n", style="bold")
        t.append(f"branch: {status.main_status}", style="bold")
        if status.main_error:
            t.append(f"\n  main: {status.main_error}", style="red")
        if status.is_service and status.dev_status:
            t.append(f"\n  dev: {status.dev_status}", style="bold")
            if status.dev_error:
                t.append(f"\n    {status.dev_error}", style="red")
        t.append(
            f"\n\nissues: {status.open_issues}  prs: {status.open_prs} "
            f"(drafts: {status.draft_prs})\n"
        )
        t.append(f"\nworst severity: {health.worst_severity.name}\n", style="bold")
        t.append(f"score: {health.score}\n\n")
        if health.findings:
            t.append("findings:\n", style="bold")
            for finding in health.findings:
                marker = (
                    "critical"
                    if finding.severity == Severity.CRITICAL
                    else finding.severity.name.lower()
                )
                summary_line = f"  [{marker}] {finding.check_name}: {finding.summary}\n"
                if finding.link:
                    # "link URL" lets the terminal emit an OSC-8 hyperlink
                    # so middle-click (or Ctrl+click) opens finding.link.
                    t.append(summary_line, style=f"link {finding.link}")
                    t.append(f"      {finding.link}\n", style=f"dim link {finding.link}")
                else:
                    t.append(summary_line)
        else:
            t.append("no findings.\n", style="dim")
        t.append("\npress o or enter to open on GitHub; esc to close.", style="dim")
        return t

    def action_open_browser(self) -> None:
        webbrowser.open(f"https://github.com/{self._health.status.full_name}")

    def action_dismiss(self, result: None = None) -> None:  # type: ignore[override]
        self.dismiss()

    def on_click(self, event: events.Click) -> None:
        """Right-click anywhere on the drilldown closes the screen."""
        if event.button == 3:
            self.dismiss()
