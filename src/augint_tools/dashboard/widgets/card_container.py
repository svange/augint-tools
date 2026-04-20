"""CardContainer -- holds RepoCards and delegates arrangement to a LayoutStrategy."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.containers import Container
from textual.widgets import Static

from ..layouts import LayoutContext, get_layout

if TYPE_CHECKING:
    from ..state import AppState
    from .repo_card import RepoCard


class _GroupHeader(Static):
    DEFAULT_CSS = ""


class _GroupSpacer(Static):
    """Invisible spacer that occupies a grid cell without rendering content.

    Inserted after a group's cards to pad the row so the next group header
    starts at column 0 (works around Textual's grid auto-placement not
    wrapping spanning widgets to the next row).
    """

    DEFAULT_CSS = """
    _GroupSpacer {
        height: 0;
        min-height: 0;
        padding: 0;
        margin: 0;
    }
    """


class CardContainer(Container):
    """Container for a dynamic set of RepoCards."""

    DEFAULT_CSS = """
    CardContainer {
        height: 1fr;
    }
    """

    def __init__(self, id: str | None = "card-container") -> None:
        super().__init__(id=id)
        self._cards: list[RepoCard] = []

    def set_cards(self, cards: list[RepoCard]) -> None:
        """Reconcile the current card set without remounting unchanged cards.

        Removes cards that aren't in ``cards``, mounts new ones, and reorders
        with ``move_child`` so widgets persist across rerenders. Group headers
        (non-RepoCard children) are always removed -- callers use
        ``set_group_headers`` when they want them.
        """
        from .repo_card import RepoCard as _RepoCard

        self._cards = list(cards)
        desired = set(cards)

        # Remove stale cards and any group headers.
        for child in list(self.children):
            if isinstance(child, _RepoCard):
                if child not in desired:
                    child.remove()
            else:
                child.remove()

        # Mount new cards (only those not already children).
        existing = {c for c in self.children if isinstance(c, _RepoCard)}
        for card in cards:
            if card not in existing:
                self.mount(card)

        # Reorder so mounted order matches ``cards``.
        for idx, card in enumerate(cards):
            try:
                self.move_child(card, before=idx)
            except Exception:
                pass

    def apply_layout(self, name: str, state: AppState, available_width: int = 0) -> None:
        """Apply the named layout strategy to the currently-mounted cards."""
        strategy = get_layout(name)
        ctx = LayoutContext(state=state, available_width=available_width)
        strategy.apply(self, self._cards, ctx)

    def clear_group_headers(self) -> None:
        """Remove any group-header and spacer widgets; cards keep their order."""
        from .repo_card import RepoCard as _RepoCard

        for child in list(self.children):
            if not isinstance(child, _RepoCard):
                child.remove()

    def set_group_headers(
        self,
        order: list[str],
        headers: dict[str, str],
        buckets: dict[str, list[RepoCard]],
        *,
        columns: int = 0,
    ) -> None:
        """Rebuild children: header, cards, header, cards, ... per ``order``.

        Cards are kept; only headers are torn down and rebuilt, then cards are
        reordered via move_child to land between headers. When ``columns`` is
        given, each header's ``column_span`` is set before mounting so the
        header stretches across the full grid width.
        """
        from .repo_card import RepoCard as _RepoCard

        # Remove only the headers; keep cards mounted.
        for child in list(self.children):
            if not isinstance(child, _RepoCard):
                child.remove()

        # Build the target child sequence:
        #   [header, *cards, *spacers, header, *cards, *spacers, ...]
        # Spacers pad incomplete rows so the next header's column_span
        # starts at column 0.  Without them Textual's grid auto-placement
        # lets a spanning widget start mid-row (it only checks that cells
        # are unoccupied, and out-of-bounds cells are never occupied).
        target: list = []
        last_idx = len(order) - 1
        for idx, team_key in enumerate(order):
            header = _GroupHeader(headers.get(team_key, team_key), classes="group-header")
            if columns > 0:
                header.styles.column_span = columns
            target.append(header)
            group_cards = buckets.get(team_key, [])
            target.extend(group_cards)
            # Pad the row so the next header lands on column 0.
            if columns > 1 and idx < last_idx:
                remainder = len(group_cards) % columns
                if remainder:
                    for _ in range(columns - remainder):
                        target.append(_GroupSpacer(""))

        # Mount headers (new) and reorder everything.
        existing = set(self.children)
        for widget in target:
            if widget not in existing:
                self.mount(widget)
        for idx, widget in enumerate(target):
            try:
                self.move_child(widget, before=idx)
            except Exception:
                pass
