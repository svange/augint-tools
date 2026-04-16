"""Layout strategy protocol and registry.

A layout decides how `RepoCard` widgets are arranged inside the
`CardContainer`. Each strategy sets CSS classes / grid templates on the
container and cards; it does not render them. Strategies register themselves
at import time via :func:`register_layout`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ..state import AppState
    from ..widgets.card_container import CardContainer
    from ..widgets.repo_card import RepoCard


@dataclass(frozen=True)
class LayoutContext:
    """Runtime context passed to LayoutStrategy.apply()."""

    state: AppState
    available_width: int


class LayoutStrategy(Protocol):
    """A named arrangement of repo cards inside a container."""

    name: str

    def apply(
        self,
        container: CardContainer,
        cards: list[RepoCard],
        ctx: LayoutContext,
    ) -> None: ...


_LAYOUTS: dict[str, LayoutStrategy] = {}
_PRIORITIES: dict[str, int] = {}


def register_layout(strategy: LayoutStrategy, *, priority: int | None = None) -> None:
    """Register a layout strategy.

    ``priority`` controls cycle order (lower runs earlier). Strategies may
    declare a class-level ``priority`` attribute; the function arg overrides.
    Unset priorities default to 100, so user-registered layouts append after
    builtins (which use 10..40).
    """
    _LAYOUTS[strategy.name] = strategy
    resolved = priority if priority is not None else getattr(strategy, "priority", 100)
    _PRIORITIES[strategy.name] = resolved


def get_layout(name: str) -> LayoutStrategy:
    try:
        return _LAYOUTS[name]
    except KeyError as exc:
        raise KeyError(f"Unknown layout '{name}'. Available: {list_layouts()}") from exc


def list_layouts() -> list[str]:
    """Return registered layout names sorted by priority then name."""
    return sorted(_LAYOUTS.keys(), key=lambda n: (_PRIORITIES.get(n, 100), n))


def _register_builtins() -> None:
    """Import builtin layouts so they self-register."""
    from . import dense as _dense  # noqa: F401
    from . import grouped as _grouped  # noqa: F401
    from . import list as _list  # noqa: F401
    from . import packed as _packed  # noqa: F401


_register_builtins()

__all__ = [
    "LayoutContext",
    "LayoutStrategy",
    "get_layout",
    "list_layouts",
    "register_layout",
]
