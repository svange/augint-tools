"""FilterPanel -- sectioned multi-select filter overlay.

Selections apply live -- each toggle posts :class:`FilterChanged` so the
main screen can rebuild the grid immediately.  The panel is still
dismissible with ``escape``/``f``; we return the final selection set
from ``dismiss`` so callers that want the list have it.

The panel is organised into sections (Orgs, Teams, GitHub Visibility, Workspace,
Status) each with their own SelectionList and All/None toggle buttons.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual import events
from textual.binding import Binding
from textual.containers import Container, Horizontal, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Rule, SelectionList, Static
from textual.widgets.selection_list import Selection

from ..state import available_filter_sections
from ..widgets.status_bar import describe_filter

if TYPE_CHECKING:
    from ..state import AppState


class FilterSection(Container):
    """A labelled group of filter checkboxes with All/None toggle buttons."""

    DEFAULT_CSS = """
    FilterSection {
        height: auto;
        padding: 0 0 1 0;
    }
    FilterSection .section-header {
        height: 1;
        width: 100%;
        padding: 0;
    }
    FilterSection .section-title {
        width: 1fr;
        text-style: bold;
    }
    FilterSection .section-btn {
        min-width: 6;
        height: 1;
        border: none;
        padding: 0 1;
        margin: 0;
        background: $surface;
        color: $text-muted;
    }
    FilterSection .section-btn:hover {
        background: $boost;
        color: $text;
    }
    FilterSection SelectionList {
        height: auto;
        max-height: 100%;
        padding: 0;
        margin: 0;
    }
    """

    def __init__(
        self,
        title: str,
        section_id: str,
        selections: list[Selection[str]],
    ) -> None:
        super().__init__(id=f"section-{section_id}")
        self._title = title
        self._section_id = section_id
        self._selections = selections

    def compose(self):
        with Horizontal(classes="section-header"):
            yield Static(self._title, classes="section-title")
            yield Button("All", id=f"{self._section_id}-all", classes="section-btn")
            yield Button("None", id=f"{self._section_id}-none", classes="section-btn")
        yield SelectionList[str](*self._selections, id=f"{self._section_id}-list")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        sel_list = self.query_one(SelectionList)
        if event.button.id and event.button.id.endswith("-all"):
            sel_list.select_all()
        elif event.button.id and event.button.id.endswith("-none"):
            sel_list.deselect_all()


class FilterPanel(ModalScreen[set[str]]):
    """Modal that lets the user toggle multiple filters on/off."""

    DEFAULT_CSS = """
    FilterPanel {
        align: center middle;
    }
    FilterPanel > Container {
        width: 58;
        max-height: 80%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    FilterPanel VerticalScroll {
        height: auto;
        max-height: 100%;
    }
    FilterPanel Rule {
        margin: 0 0 1 0;
        color: $accent;
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
        """

        def __init__(self, selected: set[str]) -> None:
            super().__init__()
            self.selected = selected

    def __init__(self, state: AppState) -> None:
        super().__init__()
        self._state = state

    def _make_selections(
        self, modes: list[str], team_labels: dict[str, str]
    ) -> list[Selection[str]]:
        return [
            Selection(
                describe_filter(mode, team_labels),
                mode,
                initial_state=mode in self._state.active_filters,
            )
            for mode in modes
        ]

    def compose(self):
        sections = available_filter_sections(
            self._state.team_labels, self._state.repo_teams, self._state.healths
        )
        tl = self._state.team_labels
        has_identity = bool(sections.orgs) or bool(sections.teams)

        with Container():
            with VerticalScroll():
                if sections.orgs:
                    yield FilterSection(
                        "Organizations", "orgs", self._make_selections(sections.orgs, tl)
                    )
                if sections.teams:
                    yield FilterSection("Teams", "teams", self._make_selections(sections.teams, tl))
                if has_identity:
                    yield Rule()
                yield FilterSection(
                    "GitHub Visibility",
                    "visibility",
                    self._make_selections(sections.visibility, tl),
                )
                yield FilterSection("Status", "health", self._make_selections(sections.health, tl))

    def _collect_selected(self) -> set[str]:
        merged: set[str] = set()
        for sl in self.query(SelectionList):
            merged.update(sl.selected)
        return merged

    def on_click(self, event: events.Click) -> None:
        if event.button == 3:
            self.action_dismiss_panel()

    def on_selection_list_selected_changed(self, event: SelectionList.SelectedChanged) -> None:
        """Apply the user's selection as soon as they toggle a filter."""
        event.stop()
        self.post_message(self.FilterChanged(self._collect_selected()))

    def action_dismiss_panel(self) -> None:
        self.dismiss(self._collect_selected())
