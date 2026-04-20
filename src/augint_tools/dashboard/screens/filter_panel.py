"""FilterPanel -- multi-select filter overlay.

Selections apply live -- each toggle posts :class:`FilterChanged` so the
main screen can rebuild the grid immediately.  The panel is still
dismissible with ``escape``/``f``; we return the final selection set
from ``dismiss`` so callers that want the list have it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual import events
from textual.binding import Binding
from textual.containers import Container
from textual.message import Message
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

    class FilterChanged(Message):
        """Emitted whenever the SelectionList selection changes.

        Carries the current selected filter keys so the main screen can
        apply them live without waiting for the panel to be dismissed.
        This matches the mental model of checkboxes on a filter bar --
        toggling one immediately filters the view underneath.
        """

        def __init__(self, selected: set[str]) -> None:
            super().__init__()
            self.selected = selected

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

    def on_selection_list_selected_changed(self, event: SelectionList.SelectedChanged) -> None:
        """Apply the user's selection as soon as they toggle a filter.

        Without this handler the grid only re-filters when the user
        dismisses the panel, which feels like the app has hung for
        anyone expecting live feedback from the checkboxes.
        """
        event.stop()
        selected = set(event.selection_list.selected)
        self.post_message(self.FilterChanged(selected))

    def action_dismiss_panel(self) -> None:
        sel_list = self.query_one("#filter-list", SelectionList)
        self.dismiss(set(sel_list.selected))
