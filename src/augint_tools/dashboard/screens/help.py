"""HelpScreen -- keybindings + controls (absorbs what was v1's Controls panel)."""

from __future__ import annotations

from rich.text import Text
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Static

_HELP_TEXT = """ai-gh dashboard -- controls

Navigation
  h j k l / arrows    Move selection
  enter               Open repo detail
  o                   Open repo on github.com

Data
  r                   Refresh now
  s                   Cycle sort (health, alpha, problem)
  f                   Open filter panel (multi-select)
  w                   Toggle workspace filter

Layouts and themes
  g                   Cycle layout (packed, grouped, severity, dense, list)
  t                   Cycle theme
  ctrl + scroll       Resize card width

Overlays
  d                   Repo detail drawer
  u                   Usage breakdown drawer
  e                   Toggle error drawer
  E                   Clear errors
  ?                   This help
  /                   Command palette
  esc                 Dismiss

Development
  F5                  Full restart (re-exec process)

Mouse
  left                Select / open details
  middle              Open Actions tab
  right               Dismiss drawer

Press ? or esc to dismiss."""


class HelpScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("question_mark", "dismiss", "Close"),
    ]

    def compose(self):
        body = Static(Text(_HELP_TEXT), id="help-body")
        yield Container(body)

    def action_dismiss(self, result: None = None) -> None:  # type: ignore[override]
        self.dismiss()
