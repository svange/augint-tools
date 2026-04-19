"""Packed layout -- cards flow left-to-right at default width. Default."""

from __future__ import annotations

from ..state import PANEL_WIDTH_DEFAULT
from . import LayoutContext, register_layout


class PackedLayout:
    name = "packed"
    priority = 10

    def apply(self, container, cards, ctx: LayoutContext) -> None:
        container.remove_class(
            "layout--grouped", "layout--dense", "layout--list", "layout--severity"
        )
        container.add_class("layout--packed")
        container.clear_group_headers()
        width = ctx.state.panel_width or PANEL_WIDTH_DEFAULT
        # Narrower cards -> more columns so the grid still fills the window.
        # +2 accounts for grid gutter; fall back to 4 when width is unknown.
        if ctx.available_width > 0:
            columns = max(1, ctx.available_width // max(10, width + 2))
        else:
            columns = 4
        try:
            container.styles.grid_size_columns = columns
        except Exception:
            pass
        for card in cards:
            card.styles.width = width
            card.styles.height = None
            card.render_mode = "packed"
            card.remove_class("card--dense", "card--list")


register_layout(PackedLayout())
