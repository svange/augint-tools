"""TopDrawer -- top-docked slide-in panel for org-wide stats.

Unlike the right-side :class:`Drawer`, TopDrawer stays in the base layer
and docks at the top of the screen. When opened it animates its height
from 0 to N rows, pushing the card grid downward rather than hovering
over it. This keeps org-wide context and repo cards visible together.

The body is split into a left and right column so the org drawer can
pack extra nerdy widgets on the right without crowding the main stack.
"""

from __future__ import annotations

from rich.text import Text
from textual import events
from textual.containers import Container
from textual.widgets import Static


class TopDrawer(Container):
    """Top-docked drawer that displaces the card grid when open."""

    DEFAULT_CSS = """
    TopDrawer {
        dock: top;
        height: 0;
        padding: 0 2;
        overflow: hidden auto;
        transition: height 180ms in_out_cubic;
        layout: horizontal;
    }
    TopDrawer.open {
        height: 32;
        padding: 1 2;
    }
    TopDrawer > #top-drawer-left {
        width: 1fr;
        padding-right: 2;
    }
    TopDrawer > #top-drawer-middle {
        width: 1fr;
        padding: 0 2;
    }
    TopDrawer > #top-drawer-right {
        width: 1fr;
        padding-left: 2;
    }
    """

    def __init__(self, id: str = "top-drawer") -> None:
        super().__init__(id=id)
        self._left = Static("", id="top-drawer-left")
        self._middle = Static("", id="top-drawer-middle")
        self._right = Static("", id="top-drawer-right")

    def compose(self):
        yield self._left
        yield self._middle
        yield self._right

    @property
    def is_open(self) -> bool:
        return self.has_class("open")

    def set_content(
        self,
        left: Text,
        middle: Text | None = None,
        right: Text | None = None,
    ) -> None:
        """Update the body without changing open/closed state."""
        self._left.update(left)
        self._middle.update(middle if middle is not None else Text(""))
        self._right.update(right if right is not None else Text(""))

    def toggle(
        self,
        left: Text,
        middle: Text | None = None,
        right: Text | None = None,
    ) -> None:
        """Open the drawer with the column contents, or close if already open."""
        if self.is_open:
            self.close()
            return
        self.set_content(left, middle, right)
        self.add_class("open")

    def open_with(
        self,
        left: Text,
        middle: Text | None = None,
        right: Text | None = None,
    ) -> None:
        self.set_content(left, middle, right)
        self.add_class("open")

    def close(self) -> None:
        self.remove_class("open")

    def on_click(self, event: events.Click) -> None:
        if event.button == 3:
            self.close()
