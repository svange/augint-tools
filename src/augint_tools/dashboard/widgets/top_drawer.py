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
        self._set_column(self._left, _normalise(left))
        self._set_column(self._middle, _normalise(middle))
        self._set_column(self._right, _normalise(right))

    def _set_column(self, column: Container, sections: list[Section]) -> None:
        """Update a column to host exactly *sections* in order.

        Reconciles in place: existing widgets with matching section ids
        have their content updated via ``Static.update()``; only truly
        new/removed sections trigger ``mount()``/``remove()``.  This
        avoids the flicker caused by tearing down all children and the
        ``DuplicateIds`` crash caused by async ``remove()`` not finishing
        before the next ``mount()`` with the same id.
        """
        prefix = _SECTION_ID_PREFIX

        # 1. Index existing section widgets by their section id.
        existing: dict[str, Static] = {}
        for child in list(column.children):
            wid = getattr(child, "id", None) or ""
            if isinstance(child, Static) and wid.startswith(prefix):
                existing[wid[len(prefix) :]] = child
            else:
                child.remove()

        desired_ids = {sid for sid, _ in sections}

        # 2. Remove stale sections no longer in the desired list.
        for stale_id in [sid for sid in existing if sid not in desired_ids]:
            existing.pop(stale_id).remove()

        # 3. Update or mount each desired section.
        for section_id, text in sections:
            widget = existing.get(section_id)
            if widget is not None:
                widget.update(text)
            else:
                static = Static(
                    text,
                    id=f"{prefix}{section_id}",
                    classes="top-drawer-section",
                )
                try:
                    column.mount(static)
                except Exception:
                    # Guard against race: an old widget with the same id
                    # may not have been fully removed yet.
                    pass

        # 4. Ensure order matches the desired sequence.
        for idx, (section_id, _) in enumerate(sections):
            try:
                w = existing.get(section_id) or column.query_one(f"#{prefix}{section_id}", Static)
                column.move_child(w, before=idx)
            except Exception:
                pass  # Settles on the next refresh cycle.

    def toggle(
        self,
        left: list[Section] | Text,
        middle: list[Section] | Text | None = None,
        right: list[Section] | Text | None = None,
    ) -> None:
        """Open the drawer with the column contents, or close if already open."""
        if self.is_open:
            self.close()
            return
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
