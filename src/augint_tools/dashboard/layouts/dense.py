"""Dense layout -- narrow cards (~24 cols), 3-line bodies, max information density."""

from __future__ import annotations

from . import LayoutContext, register_layout


class DenseLayout:
    name = "dense"
    priority = 30

    def apply(self, container, cards, ctx: LayoutContext) -> None:
        container.remove_class("layout--packed", "layout--grouped", "layout--list")
        container.add_class("layout--dense")
        container.clear_group_headers()
        width = 24
        if ctx.available_width > 0:
            columns = max(1, ctx.available_width // max(10, width + 2))
        else:
            columns = 6
        try:
            container.styles.grid_size_columns = columns
        except Exception:
            pass
        for card in cards:
            card.styles.width = width
            card.styles.height = None
            card.render_mode = "dense"
            card.remove_class("card--list")
            card.add_class("card--dense")


register_layout(DenseLayout())
