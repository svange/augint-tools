"""Drawer -- right-docked slide-in panel. CSS offset transition does the animation."""

from __future__ import annotations

from typing import Literal

from rich.text import Text
from textual import events
from textual.containers import Container
from textual.widgets import Static

DrawerMode = Literal["detail"]


class Drawer(Container):
    """Right-docked overlay. Toggled via CSS class 'open'.

    The drawer lives on the ``overlay`` layer so it hovers above the card
    grid rather than reserving docked space -- the grid keeps its full width
    whether the drawer is open or closed.
    """

    DEFAULT_CSS = """
    Drawer {
        layer: overlay;
        dock: right;
        width: 48;
        height: 100%;
        offset: 48 0;
        padding: 1 2;
        transition: offset 180ms in_out_cubic;
    }
    Drawer.open {
        offset: 0 0;
    }
    """

    def __init__(self, id: str = "drawer") -> None:
        super().__init__(id=id)
        self._body = Static("", id="drawer-body")
        self._mode: DrawerMode = "detail"

    def compose(self):
        yield self._body

    @property
    def is_open(self) -> bool:
        return self.has_class("open")

    @property
    def mode(self) -> DrawerMode:
        return self._mode

    def set_content(self, content: Text) -> None:
        """Update the drawer body without changing its open/closed state."""
        self._body.update(content)

    def toggle(self, mode: DrawerMode, content: Text) -> None:
        """Open the drawer in the given mode, or close if already open in that mode."""
        if self.is_open and self._mode == mode:
            self.close()
            return
        self._mode = mode
        self._body.update(content)
        self.add_class("open")

    def open_with(self, mode: DrawerMode, content: Text) -> None:
        self._mode = mode
        self._body.update(content)
        self.add_class("open")

    def close(self) -> None:
        self.remove_class("open")

    def on_click(self, event: events.Click) -> None:
        if event.button == 3:
            self.close()
