"""Severity layout -- cards grouped by health severity (critical -> warning -> ok)."""

from __future__ import annotations

from ..health import Severity
from ..state import PANEL_WIDTH_DEFAULT
from . import LayoutContext, register_layout

_SEVERITY_ORDER = ("critical", "warning", "ok")
_SEVERITY_LABELS = {
    "critical": "Critical",
    "warning": "Warning",
    "ok": "Healthy",
}


def _card_severity_bucket(health) -> str:
    """Classify a RepoHealth into critical/warning/ok."""
    if health is None:
        return "ok"
    status = health.status
    if status.main_status == "failure" or status.dev_status == "failure":
        return "critical"
    worst = health.worst_severity
    if worst == Severity.CRITICAL:
        return "critical"
    if worst in (Severity.HIGH, Severity.MEDIUM):
        return "warning"
    return "ok"


class SeverityLayout:
    name = "severity"
    priority = 25

    def apply(self, container, cards, ctx: LayoutContext) -> None:
        container.remove_class("layout--packed", "layout--grouped", "layout--dense", "layout--list")
        container.add_class("layout--severity")
        width = ctx.state.panel_width or PANEL_WIDTH_DEFAULT
        if ctx.available_width > 0:
            columns = max(1, ctx.available_width // max(10, width + 2))
        else:
            columns = 4
        try:
            container.styles.grid_size_columns = columns
        except Exception:
            pass

        # Bucket cards by severity.
        buckets: dict[str, list] = {key: [] for key in _SEVERITY_ORDER}
        for card in cards:
            bucket = _card_severity_bucket(card.health)
            buckets[bucket].append(card)

        # Only show sections that have cards.
        order = [key for key in _SEVERITY_ORDER if buckets[key]]
        headers = {key: _SEVERITY_LABELS[key] for key in order}
        container.set_group_headers(order, headers, buckets)

        for card in cards:
            card.styles.width = width
            card.styles.height = None
            card.render_mode = "packed"
            card.remove_class("card--dense", "card--list")


register_layout(SeverityLayout())
