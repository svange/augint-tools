"""FilterPanel -- multi-select filter overlay."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual import events
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import SelectionList
from textual.widgets.selection_list import Selection

from ..state import available_filter_modes
from ..widgets.status_bar import describe_filter

if TYPE_CHECKING:
    from ..state import AppState


class FilterPanel(ModalScreen[set[str]]):
    """Modal that lets the user toggle multiple filters on/off."""

    DEFAULT_CSS = """
    FilterPanel {
        align: center middle;
    }
    FilterPanel > Container {
        width: 50;
        max-height: 80%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    FilterPanel SelectionList {
        height: auto;
        max-height: 100%;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_panel", "Close"),
        Binding("f", "dismiss_panel", "Close"),
    ]

    def __init__(self, state: AppState) -> None:
        super().__init__()
        self._state = state

    def compose(self):
        modes = available_filter_modes(
            self._state.team_labels, self._state.repo_teams, self._state.healths
        )
        # Skip "all" -- empty selection already means all repos.
        selections = [
            Selection(
                describe_filter(mode, self._state.team_labels),
                mode,
                initial_state=mode in self._state.active_filters,
            )
            for mode in modes
            if mode != "all"
        ]
        yield Container(SelectionList[str](*selections, id="filter-list"))

    def on_click(self, event: events.Click) -> None:
        if event.button == 3:
            self.action_dismiss_panel()

    def action_dismiss_panel(self) -> None:
        sel_list = self.query_one("#filter-list", SelectionList)
        self.dismiss(set(sel_list.selected))
