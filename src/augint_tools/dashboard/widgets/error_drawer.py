"""ErrorDrawer -- bottom-docked panel for recent errors.

Sits above the DashboardFooter and animates open/closed by height,
pushing the card grid upward (same displacement approach as TopDrawer).
Auto-opens when new errors arrive; the user can dismiss it with ``e``
or right-click.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual import events
from textual.containers import Container
from textual.widgets import Static

if TYPE_CHECKING:
    from ..state import AppState


class ErrorDrawer(Container):
    """Bottom-docked error log drawer."""

    DEFAULT_CSS = """
    ErrorDrawer {
        dock: bottom;
        height: 0;
        padding: 0 1;
        overflow: hidden auto;
        transition: height 180ms in_out_cubic;
    }
    ErrorDrawer.open {
        height: 8;
        padding: 1 1;
    }
    ErrorDrawer > #error-drawer-body {
        width: 1fr;
    }
    """

    def __init__(self, id: str = "error-drawer") -> None:
        super().__init__(id=id)
        self._body = Static("", id="error-drawer-body")
        self._last_error_count: int = 0

    def compose(self):
        yield self._body

    @property
    def is_open(self) -> bool:
        return self.has_class("open")

    def refresh_content(self, state: AppState) -> bool:
        """Re-render from state. Returns True if new errors appeared."""
        new_count = len(state.errors)
        has_new = new_count > self._last_error_count
        self._last_error_count = new_count

        if not state.errors:
            self._body.update(Text("no errors recorded", style="dim"))
            return False

        t = Text()
        # Show the most recent errors (newest last), capped to keep it compact.
        recent = state.errors[-5:]
        for entry in recent:
            ts = entry.timestamp.strftime("%H:%M:%S")
            t.append(f"{ts} ", style="dim")
            t.append(f"[{entry.source}] ", style="bold red")
            t.append(entry.message)
            t.append("\n")
        total = len(state.errors)
        if total > 5:
            t.append(f"({total - 5} older errors hidden) ", style="dim")
        t.append("press e to dismiss, shift+e to clear", style="dim")
        self._body.update(t)
        return has_new

    def open(self) -> None:
        self.add_class("open")

    def close(self) -> None:
        self.remove_class("open")

    def toggle(self) -> None:
        if self.is_open:
            self.close()
        else:
            self.open()

    def on_click(self, event: events.Click) -> None:
        if event.button == 3:
            self.close()
