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


def _column_children_ids(column: Container) -> list[str]:
    """Snapshot the ids of ``column``'s current children.

    Used in log lines so we can see exactly which siblings were already
    present when a DuplicateIds-style collision occurred.
    """
    out: list[str] = []
    try:
        for child in column.children:
            wid = getattr(child, "id", None)
            out.append(wid if wid is not None else "<no-id>")
    except Exception:
        # Never let logging crash the render path.
        pass
    return out


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
            logger.info(
                "top_drawer.set_content: columns not mounted yet, skipping "
                f"(is_open={self.is_open})"
            )
            return
        left_sections = _normalise(left)
        middle_sections = _normalise(middle)
        right_sections = _normalise(right)
        logger.info(
            "top_drawer.set_content: reconciling columns "
            f"left={[sid for sid, _ in left_sections]} "
            f"middle={[sid for sid, _ in middle_sections]} "
            f"right={[sid for sid, _ in right_sections]}"
        )
        self._reconcile_column(self._left, left_sections, column_name="left")
        self._reconcile_column(self._middle, middle_sections, column_name="middle")
        self._reconcile_column(self._right, right_sections, column_name="right")

    def _columns_ready(self) -> bool:
        """True when all three column containers are mounted and usable."""
        for column in (self._left, self._middle, self._right):
            if not getattr(column, "is_mounted", False):
                return False
        return True

    def _reconcile_column(
        self,
        column: Container,
        sections: list[Section],
        *,
        column_name: str = "?",
    ) -> None:
        """Update ``column`` to host exactly ``sections`` in order.

        Reconciles in place rather than tearing down and rebuilding: existing
        Static widgets with matching section ids have their content updated
        via ``Static.update()``; only added/removed sections trigger
        ``mount()`` / ``remove()``. This avoids the flicker where rapid
        ``set_content`` calls (from refresh ticks while the drawer is open)
        leave the column momentarily empty between async remove and mount.

        Hardened against re-entrant calls: collecting ``existing`` reads
        ``column.children`` directly (which reflects pending mounts), and
        every ``mount()`` is wrapped in a guarded helper that swallows
        ``DuplicateIds`` so a render race cannot crash the toggle handler.
        """
        before_ids = _column_children_ids(column)
        # Build a map of currently-mounted Static widgets keyed by section id.
        # Walk ``column.children`` once -- this includes widgets whose mount
        # is still in flight, which is critical for survival of re-entrant
        # set_content calls fired by background ticks.
        existing: dict[str, Static] = {}
        for child in list(column.children):
            wid = getattr(child, "id", None) or ""
            if wid.startswith(_SECTION_ID_PREFIX) and isinstance(child, Static):
                section_key = wid[len(_SECTION_ID_PREFIX) :]
                if section_key in existing:
                    # A duplicate already snuck in (prior race). Drop the
                    # extra so we don't hand the same id back to mount().
                    logger.warning(
                        f"top_drawer.reconcile.duplicate_in_column[{column_name}]: "
                        f"removing extra widget for section {section_key!r}"
                    )
                    try:
                        child.remove()
                    except Exception as exc:
                        logger.exception(
                            f"top_drawer.reconcile.duplicate_remove_failed[{column_name}]: "
                            f"{exc.__class__.__name__}: {exc}"
                        )
                    continue
                existing[section_key] = child
            else:
                # Unknown child (legacy or stale) -- drop it.
                try:
                    child.remove()
                except Exception as exc:
                    logger.exception(
                        f"top_drawer.reconcile.unknown_remove_failed[{column_name}]: "
                        f"{exc.__class__.__name__}: {exc}"
                    )

        desired_ids = {section_id for section_id, _ in sections}

        # Remove sections that no longer appear in the desired list.
        stale_ids = [sid for sid in existing if sid not in desired_ids]
        for stale_id in stale_ids:
            logger.info(
                f"top_drawer.remove_section[{column_name}]: section={stale_id!r} "
                f"id={_SECTION_ID_PREFIX}{stale_id}"
            )
            try:
                existing[stale_id].remove()
            except Exception as exc:
                logger.exception(
                    f"top_drawer.remove_section_failed[{column_name}]: "
                    f"section={stale_id!r}: {exc.__class__.__name__}: {exc}"
                )
            del existing[stale_id]

        # Update or mount each desired section in order.
        mounted_count = 0
        updated_count = 0
        for section_id, text in sections:
            widget = existing.get(section_id)
            full_id = f"{_SECTION_ID_PREFIX}{section_id}"
            if widget is None:
                # Last-chance dedupe: query the column directly. mount()
                # raises DuplicateIds if any child (including a widget we
                # missed because remove() is still pending) has the same id.
                try:
                    pre_existing = column.query(f"#{full_id}")
                    if len(pre_existing) > 0:
                        # Reuse the in-DOM widget; update its content rather
                        # than mount a duplicate.
                        candidate = pre_existing.first()
                        if isinstance(candidate, Static):
                            logger.info(
                                f"top_drawer.adopt_pending[{column_name}]: "
                                f"section={section_id!r} adopted existing widget "
                                f"id={full_id} (mount/remove race)"
                            )
                            candidate.update(text)
                            existing[section_id] = candidate
                            updated_count += 1
                            continue
                except Exception as exc:
                    # query() can raise during teardown; fall through to mount.
                    logger.debug(
                        f"top_drawer.predupe_query_failed[{column_name}]: "
                        f"section={section_id!r}: {exc.__class__.__name__}: {exc}"
                    )
                static = Static(
                    text,
                    id=full_id,
                    classes="top-drawer-section",
                )
                sibling_ids = _column_children_ids(column)
                logger.info(
                    f"top_drawer.mount_section[{column_name}]: section={section_id!r} "
                    f"id={full_id} siblings={sibling_ids}"
                )
                try:
                    column.mount(static)
                    mounted_count += 1
                except Exception as exc:
                    # Defensive: a mount race (column not yet mounted, or a
                    # DuplicateIds collision because remove() is still in
                    # flight) shouldn't crash the toggle handler. Log the
                    # full traceback so we can diagnose without re-running.
                    sibling_ids_now = _column_children_ids(column)
                    logger.exception(
                        f"top_drawer.mount_section_failed[{column_name}]: "
                        f"section={section_id!r} id={full_id} "
                        f"err={exc.__class__.__name__}: {exc} "
                        f"siblings_at_failure={sibling_ids_now}"
                    )
            else:
                # In-place update -- no remount, no flicker.
                widget.update(text)
                updated_count += 1

        # Reorder to match the desired sequence. move_child is a no-op when
        # the widget is already at the correct index.
        for idx, (section_id, _) in enumerate(sections):
            widget = existing.get(section_id)
            if widget is None:
                # Newly mounted above; Textual appended it at the end.
                try:
                    widget = column.query_one(f"#{_SECTION_ID_PREFIX}{section_id}", Static)
                except Exception:
                    # Mount is still pending; order will settle next tick.
                    continue
            try:
                column.move_child(widget, before=idx)
            except Exception:
                # move_child raises if the widget isn't yet a child (mount
                # is still pending). Order will settle on the next refresh.
                pass

        after_ids = _column_children_ids(column)
        logger.info(
            f"top_drawer.reconcile.done[{column_name}]: "
            f"before={before_ids} after={after_ids} "
            f"mounted={mounted_count} updated={updated_count} "
            f"removed={len(stale_ids)}"
        )

    def toggle(
        self,
        left: list[Section] | Text,
        middle: list[Section] | Text | None = None,
        right: list[Section] | Text | None = None,
    ) -> None:
        """Open the drawer with the column contents, or close if already open."""
        currently_open = self.is_open
        try:
            child_count = sum(
                len(list(c.children)) for c in (self._left, self._middle, self._right)
            )
        except Exception:
            child_count = -1
        logger.info(
            f"top_drawer.toggle: is_open={currently_open} "
            f"action={'close' if currently_open else 'open'} "
            f"total_section_children={child_count}"
        )
        if currently_open:
            self.close()
            return
        try:
            self.set_content(left, middle, right)
        except Exception as exc:
            logger.exception(
                f"top_drawer.toggle.set_content_failed: {exc.__class__.__name__}: {exc}"
            )
            # Still flip the open class -- a partial render is better than a
            # hung toggle key.
        self.add_class("open")

    def open_with(
        self,
        left: list[Section] | Text,
        middle: list[Section] | Text | None = None,
        right: list[Section] | Text | None = None,
    ) -> None:
        logger.info("top_drawer.open_with: forcing open")
        try:
            self.set_content(left, middle, right)
        except Exception as exc:
            logger.exception(
                f"top_drawer.open_with.set_content_failed: {exc.__class__.__name__}: {exc}"
            )
        self.add_class("open")

    def close(self) -> None:
        logger.info("top_drawer.close: removing 'open' class")
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
