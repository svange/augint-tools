"""TopDrawer -- top-docked slide-in panel for org-wide stats.

Unlike the right-side :class:`Drawer`, TopDrawer stays in the base layer
and docks at the top of the screen. When opened it animates its height
from 0 to N rows, pushing the card grid downward rather than hovering
over it. This keeps org-wide context and repo cards visible together.

The body is split into a left, middle, and right column. Each column is
composed of one or more "sections" rendered as individual :class:`Static`
widgets so that clicks can be attributed to a specific section (e.g. the
activity sparkline vs the weather line). When a section is clicked the
drawer posts a :class:`TopDrawer.SectionClicked` message carrying the
section id, which the app uses to open an explanatory modal.
"""

from __future__ import annotations

from loguru import logger
from rich.text import Text
from textual import events
from textual.containers import Container
from textual.message import Message
from textual.widgets import Static

# Prefix used for per-section Static widget ids. A section registered with
# id "activity" becomes a widget with DOM id "top-drawer-section-activity".
_SECTION_ID_PREFIX = "top-drawer-section-"

# Column container ids.
_COLUMN_IDS = ("top-drawer-left", "top-drawer-middle", "top-drawer-right")

# Type alias: a column is a list of (section_id, rich_text) pairs.
Section = tuple[str, Text]


class TopDrawer(Container):
    """Top-docked drawer that displaces the card grid when open."""

    DEFAULT_CSS = """
    TopDrawer {
        dock: top;
        height: 0;
        padding: 0 1;
        overflow: hidden auto;
        transition: height 180ms in_out_cubic;
        layout: horizontal;
    }
    TopDrawer.open {
        height: 32;
        padding: 1 1;
    }
    TopDrawer > #top-drawer-left {
        width: 1fr;
        padding-right: 1;
        height: auto;
    }
    TopDrawer > #top-drawer-middle {
        width: 1fr;
        padding: 0 1;
        height: auto;
    }
    TopDrawer > #top-drawer-right {
        width: 1fr;
        padding-left: 1;
        height: auto;
    }
    TopDrawer .top-drawer-section {
        height: auto;
        width: 100%;
    }
    TopDrawer .top-drawer-section:hover {
        background: $boost;
    }
    """

    class SectionClicked(Message):
        """Posted when a user clicks a specific top-drawer section."""

        def __init__(self, section_id: str) -> None:
            self.section_id = section_id
            super().__init__()

    def __init__(self, id: str = "top-drawer") -> None:
        super().__init__(id=id)
        self._left = Container(id="top-drawer-left")
        self._middle = Container(id="top-drawer-middle")
        self._right = Container(id="top-drawer-right")

    def compose(self):
        yield self._left
        yield self._middle
        yield self._right

    @property
    def is_open(self) -> bool:
        return self.has_class("open")

    # ------------------------------------------------------------------
    # Content management
    # ------------------------------------------------------------------

    def set_content(
        self,
        left: list[Section] | Text,
        middle: list[Section] | Text | None = None,
        right: list[Section] | Text | None = None,
    ) -> None:
        """Replace each column's content.

        Accepts either a list of ``(section_id, Text)`` pairs -- preferred --
        or a single :class:`~rich.text.Text` for backward compatibility. A
        raw ``Text`` is treated as a one-section column with a stub id.
        """
        # Skip while not mounted -- mount() on an unmounted Container raises
        # MountError in Textual. The first paint of an open drawer is driven
        # by toggle()/open_with() which run after compose() has mounted the
        # column containers, so this only protects against races where a
        # background tick fires before/after that window.
        if not self._columns_ready():
            logger.debug("top_drawer.set_content: columns not mounted yet, skipping")
            return
        self._reconcile_column(self._left, _normalise(left))
        self._reconcile_column(self._middle, _normalise(middle))
        self._reconcile_column(self._right, _normalise(right))

    def _columns_ready(self) -> bool:
        """True when all three column containers are mounted and usable."""
        for column in (self._left, self._middle, self._right):
            if not getattr(column, "is_mounted", False):
                return False
        return True

    def _reconcile_column(self, column: Container, sections: list[Section]) -> None:
        """Update ``column`` to host exactly ``sections`` in order.

        Reconciles in place rather than tearing down and rebuilding: existing
        Static widgets with matching section ids have their content updated
        via ``Static.update()``; only added/removed sections trigger
        ``mount()`` / ``remove()``. This avoids the flicker where rapid
        ``set_content`` calls (from refresh ticks while the drawer is open)
        leave the column momentarily empty between async remove and mount.
        """
        # Build a map of currently-mounted Static widgets keyed by section id.
        existing: dict[str, Static] = {}
        for child in list(column.children):
            wid = getattr(child, "id", None) or ""
            if wid.startswith(_SECTION_ID_PREFIX) and isinstance(child, Static):
                existing[wid[len(_SECTION_ID_PREFIX) :]] = child
            else:
                # Unknown child (legacy or stale) -- drop it.
                child.remove()

        desired_ids = {section_id for section_id, _ in sections}

        # Remove sections that no longer appear in the desired list.
        stale_ids = [sid for sid in existing if sid not in desired_ids]
        for stale_id in stale_ids:
            existing[stale_id].remove()
            del existing[stale_id]

        # Update or mount each desired section in order.
        for section_id, text in sections:
            widget = existing.get(section_id)
            if widget is None:
                static = Static(
                    text,
                    id=f"{_SECTION_ID_PREFIX}{section_id}",
                    classes="top-drawer-section",
                )
                try:
                    column.mount(static)
                except Exception as exc:
                    # Defensive: a mount race (column not yet mounted, or
                    # mid-removal) shouldn't crash the toggle handler.
                    logger.debug(
                        f"top_drawer.mount failed for section {section_id!r}: "
                        f"{exc.__class__.__name__}: {exc}"
                    )
            else:
                # In-place update -- no remount, no flicker.
                widget.update(text)

        # Reorder to match the desired sequence. move_child is a no-op when
        # the widget is already at the correct index.
        for idx, (section_id, _) in enumerate(sections):
            widget = existing.get(section_id)
            if widget is None:
                # Newly mounted above; Textual appended it at the end.
                widget = column.query_one(f"#{_SECTION_ID_PREFIX}{section_id}", Static)  # type: ignore[arg-type]
            try:
                column.move_child(widget, before=idx)
            except Exception:
                # move_child raises if the widget isn't yet a child (mount
                # is still pending). Order will settle on the next refresh.
                pass

    def toggle(
        self,
        left: list[Section] | Text,
        middle: list[Section] | Text | None = None,
        right: list[Section] | Text | None = None,
    ) -> None:
        """Open the drawer with the column contents, or close if already open."""
        if self.is_open:
            logger.debug("top_drawer.toggle: closing")
            self.close()
            return
        logger.debug("top_drawer.toggle: opening")
        self.set_content(left, middle, right)
        self.add_class("open")

    def open_with(
        self,
        left: list[Section] | Text,
        middle: list[Section] | Text | None = None,
        right: list[Section] | Text | None = None,
    ) -> None:
        self.set_content(left, middle, right)
        self.add_class("open")

    def close(self) -> None:
        self.remove_class("open")

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    def on_click(self, event: events.Click) -> None:
        # Right-click closes the drawer, matching the other drawers.
        if event.button == 3:
            self.close()
            return
        # Map left-clicks to the nearest clicked section id.
        widget = event.widget
        section_id: str | None = None
        while widget is not None and widget is not self:
            wid = getattr(widget, "id", None)
            if wid and wid.startswith(_SECTION_ID_PREFIX):
                section_id = wid[len(_SECTION_ID_PREFIX) :]
                break
            widget = getattr(widget, "parent", None)
        if section_id:
            # Post the message so the parent screen/app can react (e.g. open
            # an explanatory modal). Using a Message keeps TopDrawer unaware
            # of how the app handles the click.
            self.post_message(self.SectionClicked(section_id))


def _normalise(value: list[Section] | Text | None) -> list[Section]:
    """Coerce the various accepted content types into a list of sections."""
    if value is None:
        return []
    if isinstance(value, Text):
        return [("legacy", value)] if value.plain else []
    return list(value)
