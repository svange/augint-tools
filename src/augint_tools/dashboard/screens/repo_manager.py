"""RepoManager -- modal screen to enable/disable repos for the dashboard.

Disabled repos are excluded from API refresh cycles (saving rate-limit
budget) and hidden from every filter view.
"""

from __future__ import annotations

from textual import events
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import SelectionList, Static
from textual.widgets.selection_list import Selection


class RepoManager(ModalScreen[set[str]]):
    """Modal that lets the user toggle individual repos on/off.

    Returns the set of **disabled** repo full-names on dismiss.
    """

    DEFAULT_CSS = """
    RepoManager {
        align: center middle;
    }
    RepoManager > Container {
        width: 60;
        max-height: 80%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    RepoManager #rm-title {
        height: 1;
        padding: 0 0 1 0;
        text-style: bold;
    }
    RepoManager SelectionList {
        height: auto;
        max-height: 100%;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_panel", "Close"),
        Binding("m", "dismiss_panel", "Close"),
    ]

    def __init__(
        self,
        all_repo_names: list[str],
        disabled_repos: set[str],
    ) -> None:
        super().__init__()
        self._all_repo_names = sorted(all_repo_names)
        self._disabled_repos = disabled_repos

    def compose(self):
        # Each repo is a selection entry.  Selected = enabled, unselected = disabled.
        selections = [
            Selection(
                name,
                name,
                initial_state=name not in self._disabled_repos,
            )
            for name in self._all_repo_names
        ]
        yield Container(
            Static("Manage repos (selected = enabled, unselected = disabled)", id="rm-title"),
            SelectionList[str](*selections, id="repo-list"),
        )

    def on_click(self, event: events.Click) -> None:
        if event.button == 3:
            self.action_dismiss_panel()

    def action_dismiss_panel(self) -> None:
        sel_list = self.query_one("#repo-list", SelectionList)
        enabled = set(sel_list.selected)
        disabled = {name for name in self._all_repo_names if name not in enabled}
        self.dismiss(disabled)
