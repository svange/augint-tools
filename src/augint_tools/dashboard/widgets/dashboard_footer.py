"""Custom two-zone footer: numbered functional keys left, visual keys right.

Each key entry is a separate clickable widget that triggers the corresponding
app action on click, in addition to the existing keyboard shortcuts.
"""

from __future__ import annotations

from rich.text import Text
from textual.containers import Horizontal
from textual.events import Click
from textual.widgets import Static

# Functional keys shown on the left with [N] numbering.
# (key_char, label, action_name)
_LEFT_KEYS: list[tuple[str, str, str]] = [
    ("q", "Quit", "quit"),
    ("r", "Refresh", "refresh_now"),
    ("s", "Sort", "cycle_sort"),
    ("f", "Filter", "open_filter_panel"),
    ("g", "Layout", "cycle_layout"),
    ("t", "Theme", "cycle_theme"),
    ("b", "Blink", "toggle_flash"),
]

# Drawer/panel keys shown on the right without numbering.
# (key_char, label, action_name)
_RIGHT_KEYS: list[tuple[str, str, str]] = [
    ("W", "Org", "cycle_top_drawer"),
    ("A", "AWS", "cycle_left_drawer"),
    ("D", "Drawer", "cycle_right_drawer"),
    ("e", "Errors", "toggle_errors"),
    ("?", "Help", "show_help"),
]


class _FooterItem(Static):
    """A single clickable footer key entry."""

    DEFAULT_CSS = """
    _FooterItem {
        width: auto;
        height: 1;
        padding: 0 0;
    }
    _FooterItem:hover {
        background: #333344;
    }
    """

    def __init__(self, label: Text, action_name: str) -> None:
        super().__init__(label)
        self._action_name = action_name

    async def _on_click(self, event: Click) -> None:
        event.stop()
        await self.app.run_action(self._action_name)


def _build_left_items() -> list[_FooterItem]:
    """Create clickable items for the left (numbered) section."""
    items: list[_FooterItem] = []
    for i, (key, label, action) in enumerate(_LEFT_KEYS, 1):
        txt = Text()
        if i > 1:
            txt.append(" ")
        txt.append(f"[{i}]", style="dim")
        txt.append(" ")
        txt.append(key, style="bold")
        txt.append(f" {label}")
        items.append(_FooterItem(txt, action))
    return items


def _build_right_items() -> list[_FooterItem]:
    """Create clickable items for the right (visual) section."""
    items: list[_FooterItem] = []
    for key, label, action in _RIGHT_KEYS:
        txt = Text()
        txt.append(key, style="bold")
        txt.append(f" {label} ")
        items.append(_FooterItem(txt, action))
    return items


class _FooterSeparator(Static):
    """A dim vertical bar separator between footer sections."""

    DEFAULT_CSS = """
    _FooterSeparator {
        width: auto;
        height: 1;
        padding: 0 1;
    }
    """

    def __init__(self) -> None:
        txt = Text("|", style="dim")
        super().__init__(txt)


class DashboardFooter(Horizontal):
    """Split footer: numbered functional controls left, visual controls right."""

    DEFAULT_CSS = """
    DashboardFooter {
        dock: bottom;
        height: 1;
        overflow: hidden;
        background: #1a1a22;
        color: #8791b0;
    }
    DashboardFooter > #footer-left {
        width: 1fr;
        height: 1;
        overflow: hidden;
    }
    DashboardFooter > #footer-right {
        width: auto;
        height: 1;
        content-align: right middle;
        overflow: hidden;
    }
    """

    def __init__(self, id: str = "dashboard-footer") -> None:
        super().__init__(id=id)

    def compose(self):  # type: ignore[override]
        yield Horizontal(*_build_left_items(), id="footer-left")
        yield _FooterSeparator()
        yield Horizontal(*_build_right_items(), id="footer-right")
