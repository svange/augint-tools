"""Custom two-zone footer: numbered functional keys left, visual keys right."""

from __future__ import annotations

from rich.text import Text
from textual.containers import Container
from textual.widgets import Static

# Functional keys shown on the left with [N] numbering.
_LEFT_KEYS: list[tuple[str, str]] = [
    ("q", "Quit"),
    ("r", "Refresh"),
    ("s", "Sort"),
    ("f", "Filter"),
    ("d", "Detail"),
    ("u", "Usage"),
    ("i", "Org"),
    ("e", "Errors"),
    ("?", "Help"),
]

# Visual/presentation keys shown on the right without numbering.
_RIGHT_KEYS: list[tuple[str, str]] = [
    ("g", "Layout"),
    ("t", "Theme"),
    ("b", "Blink"),
]


class DashboardFooter(Container):
    """Split footer: numbered functional controls left, visual controls right."""

    DEFAULT_CSS = """
    DashboardFooter {
        dock: bottom;
        height: 1;
        layout: horizontal;
        overflow: hidden;
        background: #1a1a22;
        color: #8791b0;
    }
    DashboardFooter > #footer-left {
        width: 1fr;
        overflow: hidden;
    }
    DashboardFooter > #footer-right {
        width: auto;
        content-align: right middle;
        overflow: hidden;
    }
    """

    def __init__(self, id: str = "dashboard-footer") -> None:
        super().__init__(id=id)
        self._left = Static("", id="footer-left")
        self._right = Static("", id="footer-right")

    def compose(self):
        yield self._left
        yield self._right

    def on_mount(self) -> None:
        self._render_footer()

    def _render_footer(self) -> None:
        left = Text()
        for i, (key, label) in enumerate(_LEFT_KEYS, 1):
            if i > 1:
                left.append(" ")
            left.append(f"[{i}]", style="dim")
            left.append(" ")
            left.append(key, style="bold")
            left.append(f" {label}")
        self._left.update(left)

        right = Text()
        for key, label in _RIGHT_KEYS:
            right.append(key, style="bold")
            right.append(f" {label} ")
        self._right.update(right)
