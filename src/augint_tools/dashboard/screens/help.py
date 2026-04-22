"""HelpScreen -- keybindings + controls (absorbs what was v1's Controls panel)."""

from __future__ import annotations

from rich.text import Text
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Static

_HELP_TEXT = """ai-tools dashboard -- controls

Navigation
  h j k l / arrows    Move selection
  enter               Open repo detail
  o                   Open repo on github.com

Data
  r                   Refresh now
  1                   Open filter panel (multi-select)
  2                   Cycle sort (health, alpha, problem)
  m                   Manage repos (enable/disable)
  O                   Manage organizations (add/remove)
  W                   Hide/show workspace repos

Layouts and themes
  3                   Cycle layout (packed, grouped, severity, dense, list)
  4                   Cycle theme
  5                   Toggle flash/blink
  ctrl + scroll       Resize card width

Overlays
  a                   AWS profile drawer
  w                   Org drawer
  s                   System drawer (CPU, docker, network)
  d                   Repo detail / system drawer (cycle)
  e                   Toggle error drawer
  E                   Clear errors
  ?                   This help
  /                   Command palette
  esc                 Dismiss

Quit
  q                   Quit
  ctrl+c              Quit

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
