"""OrgManager -- modal screen to add/remove organizations from the dashboard.

Uses an opt-out model: all organizations are enabled by default.
The user un-checks orgs to disable them; the returned set contains the
**disabled** org logins (matching the ``disabled_repos`` pattern).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual import events
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import SelectionList, Static
from textual.widgets.selection_list import Selection

if TYPE_CHECKING:
    pass


class OrgManager(ModalScreen[set[str]]):
    """Modal that lets the user toggle organizations on/off.

    Returns the set of **disabled** org logins on dismiss (orgs the user
    un-checked).  Checked orgs are enabled.
    """

    DEFAULT_CSS = """
    OrgManager {
        align: center middle;
    }
    OrgManager > Container {
        width: 60;
        max-height: 80%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    OrgManager #om-title {
        height: 1;
        padding: 0 0 1 0;
        text-style: bold;
    }
    OrgManager SelectionList {
        height: auto;
        max-height: 100%;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_panel", "Close"),
        Binding("O", "dismiss_panel", "Close"),
    ]

    def __init__(
        self,
        available_orgs: list[str],
        disabled_orgs: set[str],
        viewer_login: str = "",
    ) -> None:
        super().__init__()
        self._available_orgs = sorted(available_orgs)
        self._disabled_orgs = disabled_orgs
        self._viewer_login = viewer_login

    def compose(self):
        selections = [
            Selection(
                f"{org_login}",
                org_login,
                initial_state=org_login not in self._disabled_orgs,
            )
            for org_login in self._available_orgs
            if org_login != self._viewer_login  # Personal account is always included.
        ]
        hint = "(personal account always included)" if self._viewer_login else ""
        yield Container(
            Static(f"Manage organizations {hint}", id="om-title"),
            SelectionList[str](*selections, id="org-list"),
        )

    def on_click(self, event: events.Click) -> None:
        if event.button == 3:
            self.action_dismiss_panel()

    def action_dismiss_panel(self) -> None:
        sel_list = self.query_one("#org-list", SelectionList)
        enabled = set(sel_list.selected)
        # Return the disabled set: orgs available (excluding personal) minus enabled.
        toggleable = {o for o in self._available_orgs if o != self._viewer_login}
        self.dismiss(toggleable - enabled)
