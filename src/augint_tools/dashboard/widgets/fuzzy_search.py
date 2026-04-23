"""Floating fuzzy-search overlay for filtering repos by name."""

from __future__ import annotations

from textual import events
from textual.containers import Container
from textual.message import Message
from textual.widgets import Input


class FuzzySearchBar(Container):
    """Overlay search bar that filters repos with fzf-style fuzzy matching."""

    DEFAULT_CSS = """
    FuzzySearchBar {
        dock: top;
        layer: overlay;
        height: auto;
        max-height: 3;
        width: 100%;
        display: none;
        padding: 0 1;
        background: transparent;
    }
    FuzzySearchBar.visible {
        display: block;
    }
    FuzzySearchBar Input {
        width: 50;
        background: $surface;
        border: round $accent;
    }
    """

    class Changed(Message):
        """Emitted when the search query text changes."""

        def __init__(self, query: str) -> None:
            super().__init__()
            self.query = query

    class Dismissed(Message):
        """Emitted when the user dismisses the search bar."""

    def compose(self):
        yield Input(placeholder="fuzzy search repos...", id="fuzzy-input", disabled=True)

    def show(self) -> None:
        """Show the search bar and focus the input."""
        inp = self.query_one(Input)
        inp.disabled = False
        self.add_class("visible")
        inp.focus()

    def hide(self) -> None:
        """Hide the search bar and clear the query."""
        inp = self.query_one(Input)
        inp.value = ""
        inp.disabled = True
        self.remove_class("visible")
        self.post_message(self.Changed(""))

    @property
    def is_visible(self) -> bool:
        return self.has_class("visible")

    def on_input_changed(self, event: Input.Changed) -> None:
        event.stop()
        self.post_message(self.Changed(event.value))

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            event.prevent_default()
            self.post_message(self.Dismissed())
