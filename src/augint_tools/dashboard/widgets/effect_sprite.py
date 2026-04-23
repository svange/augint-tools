"""EffectSprite -- small animated pixel-art sticker overlaid on a card corner.

Spawned by the app on severity-class transitions. The sprite floats above the
card grid via the ``effects`` CSS layer so it doesn't disturb layout, plays
one full animation cycle, then removes itself (auto-dismiss).

Kinds:
  sparkle   -- rising particles (green transition celebration)
  shockwave -- expanding rings (alert / attention)
  warning   -- amber ripple (non-critical warning)
  shimmer   -- left-to-right bar sweep (data refresh complete)
"""

from __future__ import annotations

from typing import Any, Literal

from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget

EffectKind = Literal["sparkle", "shockwave", "warning", "shimmer"]

SPRITE_WIDTH = 7
SPRITE_HEIGHT = 5
_FRAME_INTERVAL_SECONDS = 0.12

# ---------------------------------------------------------------------------
# Sprite definitions
#
# Each entry has:
#   "frames"  -- list[str], one string per frame. Each string must be exactly
#                SPRITE_WIDTH chars wide per line and SPRITE_HEIGHT lines tall,
#                lines separated by "\n". Pad with spaces as needed.
#   "colors"  -- list[str], one Rich style per frame. Must be same length as
#                "frames".
# ---------------------------------------------------------------------------

SPRITE_DEFS: dict[str, dict[str, Any]] = {
    # ------------------------------------------------------------------
    # sparkle -- 8 frames, rising particles: dots/colons/asterisks
    # ------------------------------------------------------------------
    "sparkle": {
        "frames": [
            "       \n       \n       \n   .   \n       ",
            "       \n       \n   .   \n  . .  \n   .   ",
            "       \n   .   \n  . .  \n . * . \n  . .  ",
            "   *   \n  .:   \n .: .: \n. * * .\n .: .: ",
            "  *:*  \n . * . \n: * * :\n * * * \n: * * :",
            " :*:*: \n:* * *:\n * * * \n * * * \n * * * ",
            " . . . \n . . . \n  . .  \n   .   \n       ",
            "       \n  . .  \n   .   \n       \n       ",
        ],
        "colors": [
            "bright_white",
            "bright_cyan",
            "bright_green",
            "green",
            "bright_cyan",
            "cyan",
            "dim bright_cyan",
            "dim cyan",
        ],
    },
    # ------------------------------------------------------------------
    # shockwave -- 8 frames, expanding concentric rings
    # ------------------------------------------------------------------
    "shockwave": {
        "frames": [
            "       \n       \n   *   \n       \n       ",
            "       \n  ---  \n  -+-  \n  ---  \n       ",
            "  ---  \n /   \\ \n |   | \n \\   / \n  ---  ",
            " ----- \n/     \\\n|     |\n\\     /\n ----- ",
            "/-----\\\n|     |\n|     |\n|     |\n\\-----/",
            "/=====\\\n|     |\n|     |\n|     |\n\\=====/",
            "/ = = \\\n       \n = = = \n       \n\\ = = /",
            "       \n       \n = = = \n       \n       ",
        ],
        "colors": [
            "bright_white",
            "bright_red",
            "red",
            "bright_red",
            "red",
            "dark_red",
            "dim red",
            "dim dark_red",
        ],
    },
    # ------------------------------------------------------------------
    # warning -- 4 frames, amber ripple using tildes
    # ------------------------------------------------------------------
    "warning": {
        "frames": [
            "       \n       \n   !   \n       \n       ",
            "  ~~~  \n ~   ~ \n ~ ! ~ \n ~   ~ \n  ~~~  ",
            " ~~~~~ \n~     ~\n~ !!! ~\n~     ~\n ~~~~~ ",
            "~~~~~~~\n~     ~\n~ !!! ~\n~     ~\n~~~~~~~",
        ],
        "colors": [
            "bright_yellow",
            "yellow",
            "bright_yellow",
            "dim yellow",
        ],
    },
    # ------------------------------------------------------------------
    # shimmer -- 7 frames, vertical bar sweeping left to right
    # ------------------------------------------------------------------
    "shimmer": {
        "frames": [
            "|      \n|      \n|      \n|      \n|      ",
            " |     \n |     \n |     \n |     \n |     ",
            "  |    \n  |    \n  |    \n  |    \n  |    ",
            "   |   \n   |   \n   |   \n   |   \n   |   ",
            "    |  \n    |  \n    |  \n    |  \n    |  ",
            "     | \n     | \n     | \n     | \n     | ",
            "      |\n      |\n      |\n      |\n      |",
        ],
        "colors": [
            "dim bright_white",
            "bold bright_white",
            "bold bright_cyan",
            "bold bright_white",
            "bold bright_cyan",
            "bold bright_white",
            "dim bright_white",
        ],
    },
}


class EffectSprite(Widget):
    """One animated pixel-art sticker. Plays once then removes itself."""

    DEFAULT_CSS = f"""
    EffectSprite {{
        layer: effects;
        width: {SPRITE_WIDTH};
        height: {SPRITE_HEIGHT};
        background: transparent;
    }}
    """

    auto_dismiss: bool = True

    frame_idx: reactive[int] = reactive(0)

    def __init__(self, kind: EffectKind, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._kind: EffectKind = kind
        defn = SPRITE_DEFS[kind]
        self._frames: list[str] = defn["frames"]
        self._colors: list[str] = defn["colors"]

    @property
    def kind(self) -> EffectKind:
        return self._kind

    def on_mount(self) -> None:
        self.set_interval(_FRAME_INTERVAL_SECONDS, self._advance)

    def _advance(self) -> None:
        next_idx = self.frame_idx + 1
        if next_idx >= len(self._frames):
            self.remove()
        else:
            self.frame_idx = next_idx

    def watch_frame_idx(self, _old: int, _new: int) -> None:
        self.refresh()

    def render(self) -> Text:
        glyphs = self._frames[self.frame_idx]
        style = self._colors[self.frame_idx]
        text = Text(glyphs, style=style)
        text.no_wrap = True
        text.overflow = "crop"
        return text
