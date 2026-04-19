"""List layout -- one card per row, full width, expanded findings."""

from __future__ import annotations

from . import LayoutContext, register_layout


class ListLayout:
    name = "list"
    priority = 40

    def apply(self, container, cards, ctx: LayoutContext) -> None:
        container.remove_class(
            "layout--packed", "layout--grouped", "layout--dense", "layout--severity"
        )
        container.add_class("layout--list")
        container.clear_group_headers()
        for card in cards:
            card.styles.width = "100%"
            card.styles.height = None
            card.render_mode = "list"
            card.remove_class("card--dense")
            card.add_class("card--list")


register_layout(ListLayout())
