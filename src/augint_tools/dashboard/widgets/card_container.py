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
        """Remove any group-header widgets; cards keep their order."""
        for child in list(self.children):
            if isinstance(child, _GroupHeader):
                child.remove()

    def set_group_headers(
        self,
        order: list[str],
        headers: dict[str, str],
        buckets: dict[str, list[RepoCard]],
    ) -> None:
        """Rebuild children: header, cards, header, cards, ... per ``order``.

        Cards are kept; only headers are torn down and rebuilt, then cards are
        reordered via move_child to land between headers.
        """
        from .repo_card import RepoCard as _RepoCard

        # Remove only the headers; keep cards mounted.
        for child in list(self.children):
            if not isinstance(child, _RepoCard):
                child.remove()

        # Build the target child sequence: [header, *cards, header, *cards, ...]
        target: list = []
        for team_key in order:
            header = _GroupHeader(headers.get(team_key, team_key), classes="group-header")
            target.append(header)
            target.extend(buckets.get(team_key, []))

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
