"""Grouped layout -- team section headers, cards flow under each section."""

from __future__ import annotations

from ..state import (
    PANEL_WIDTH_DEFAULT,
    RepoTeamInfo,
    display_team_label,
)
from . import LayoutContext, register_layout


class GroupedLayout:
    name = "grouped"
    priority = 20

    def apply(self, container, cards, ctx: LayoutContext) -> None:
        container.remove_class(
            "layout--packed", "layout--dense", "layout--list", "layout--severity"
        )
        container.add_class("layout--grouped")
        width = ctx.state.panel_width or PANEL_WIDTH_DEFAULT
        if ctx.available_width > 0:
            columns = max(1, ctx.available_width // max(10, width + 2))
        else:
            columns = 4
        try:
            container.styles.grid_size_columns = columns
        except Exception:
            pass

        # Bucket cards by their repo's primary team.
        buckets: dict[str, list] = {}
        order: list[str] = []
        for card in cards:
            info = ctx.state.repo_teams.get(card.repo_full_name, RepoTeamInfo())
            team_key = info.primary
            if team_key not in buckets:
                buckets[team_key] = []
                order.append(team_key)
            buckets[team_key].append(card)

        headers: dict[str, str] = {
            team_key: display_team_label(team_key, ctx.state.team_labels) for team_key in order
        }
        container.set_group_headers(order, headers, buckets)

        for card in cards:
            card.styles.width = width
            card.styles.height = None
            card.render_mode = "packed"
            card.remove_class("card--dense", "card--list")


register_layout(GroupedLayout())
