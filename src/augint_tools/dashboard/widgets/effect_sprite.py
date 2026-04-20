"""EffectSprite -- small animated pixel-art sticker overlaid on a card corner.

Spawned by the app on severity-class transitions (red->green = fireworks,
any->red = mushroom). The sprite floats above the card grid via the
``effects`` CSS layer so it doesn't disturb layout, and persists until any
click anywhere on the dashboard dismisses every active sprite.
"""

from __future__ import annotations

from typing import Literal

from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget

EffectKind = Literal["fireworks", "mushroom"]

SPRITE_WIDTH = 5
SPRITE_HEIGHT = 4
_FRAME_INTERVAL_SECONDS = 0.33


_FIREWORK_FRAMES: list[str] = [
    ("     \n  *  \n     \n     "),
    ("  *  \n \\|/ \n -+- \n /|\\ "),
    ("* | *\n\\\\|//\n=*+*=\n//|\\\\"),
    (". . .\n ' ' \n. * .\n ' ' "),
]

# Per-frame foreground colour. Cycles through bright celebratory hues.
_FIREWORK_COLORS: list[str] = [
    "bold yellow",
    "bold bright_magenta",
    "bold bright_cyan",
    "bold bright_white",
]


_MUSHROOM_FRAMES: list[str] = [
    ("     \n  .  \n  |  \n  |  "),
    ("     \n ___ \n  |  \n  |  "),
    (" ___ \n/###\\\n |#| \n |#| "),
    ("#####\n#####\n |#| \n |#| "),
]

_MUSHROOM_COLORS: list[str] = [
    "bold yellow",
    "bold bright_red",
    "bold red",
    "bold bright_white on red",
]


class EffectSprite(Widget):
    """One animated pixel-art sticker."""

    DEFAULT_CSS = f"""
    EffectSprite {{
        layer: effects;
        width: {SPRITE_WIDTH};
        height: {SPRITE_HEIGHT};
        background: transparent;
    }}
    """

    frame_idx: reactive[int] = reactive(0)

    def __init__(self, kind: EffectKind, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._kind: EffectKind = kind
        if kind == "fireworks":
            self._frames = _FIREWORK_FRAMES
            self._colors = _FIREWORK_COLORS
        else:
            self._frames = _MUSHROOM_FRAMES
            self._colors = _MUSHROOM_COLORS

    @property
    def kind(self) -> EffectKind:
        return self._kind

    def on_mount(self) -> None:
        self.set_interval(_FRAME_INTERVAL_SECONDS, self._advance)

    def _advance(self) -> None:
        self.frame_idx = (self.frame_idx + 1) % len(self._frames)

    def watch_frame_idx(self, _old: int, _new: int) -> None:
        self.refresh()

    def render(self) -> Text:
        glyphs = self._frames[self.frame_idx]
        style = self._colors[self.frame_idx]
        text = Text(glyphs, style=style)
        text.no_wrap = True
        text.overflow = "crop"
        return text
