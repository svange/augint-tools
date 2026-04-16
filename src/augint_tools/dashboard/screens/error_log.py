"""ErrorLogScreen -- timestamped refresh/usage/cache/ui errors with a clear button."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Static

if TYPE_CHECKING:
    from ..state import AppState


class ErrorLogScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("e", "dismiss", "Close"),
    ]

    def __init__(self, state: AppState) -> None:
        super().__init__()
        self._state = state

    def compose(self):
        body = Static(self._render_errors(), id="error-log-list")
        clear = Button("Clear errors", id="clear-errors", variant="warning")
        close = Button("Close", id="close-errors")
        yield Container(body, Horizontal(clear, close), id="error-log-body")

    def _render_errors(self) -> Text:
        if not self._state.errors:
            return Text("no errors recorded", style="dim")
        t = Text()
        for entry in reversed(self._state.errors):
            ts = entry.timestamp.strftime("%H:%M:%S")
            t.append(f"{ts} ", style="dim")
            t.append(f"[{entry.source}] ", style="bold")
            t.append(entry.message)
            t.append("\n")
        return t

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "clear-errors":
            self._state.clear_errors()
            self.query_one("#error-log-list", Static).update(self._render_errors())
        elif event.button.id == "close-errors":
            self.dismiss()

    def action_dismiss(self, result: None = None) -> None:  # type: ignore[override]
        self.dismiss()
