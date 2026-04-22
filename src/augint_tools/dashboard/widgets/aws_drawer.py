"""AwsDrawer -- left-docked drawer showing AWS SSO profiles.

Displays profile names, regions, and session status with color coding.
Clicking an expired/error profile launches SSO login.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual import events
from textual.containers import Container
from textual.message import Message
from textual.widgets import Static

if TYPE_CHECKING:
    from ..awsprobe import AwsProfile, AwsState


class AwsDrawer(Container):
    """Left-docked drawer for AWS profile info. Overlays the card grid."""

    DEFAULT_CSS = """
    AwsDrawer {
        layer: overlay;
        dock: left;
        width: 48;
        height: 100%;
        offset: -48 0;
        padding: 1 2;
        transition: offset 180ms in_out_cubic;
    }
    AwsDrawer.open {
        offset: 0 0;
    }
    """

    class SsoLoginRequested(Message):
        """Posted when user clicks an expired/error profile to trigger login."""

        def __init__(self, profile_name: str) -> None:
            self.profile_name = profile_name
            super().__init__()

    def __init__(self, id: str = "aws-drawer") -> None:
        super().__init__(id=id)
        self._body = Static("", id="aws-drawer-body")
        self._profile_lines: list[tuple[int, str]] = []  # (line_index, profile_name)

    def compose(self):
        yield self._body

    @property
    def is_open(self) -> bool:
        return self.has_class("open")

    def open(self) -> None:
        self.add_class("open")

    def close(self) -> None:
        self.remove_class("open")

    def toggle(self) -> None:
        if self.is_open:
            self.close()
        else:
            self.open()

    def refresh_content(self, aws_state: AwsState | None) -> None:
        """Rebuild the drawer body from current AWS state."""
        t = Text()
        t.append("AWS Profiles\n\n", style="bold")
        self._profile_lines.clear()

        if aws_state is None:
            t.append("  loading...\n", style="dim")
            self._body.update(t)
            return

        if not aws_state.aws_cli_available:
            t.append("  aws CLI not found\n", style="dim red")
            self._body.update(t)
            return

        if not aws_state.profiles:
            t.append("  no profiles configured\n", style="dim")
            t.append("  (~/.aws/config)\n", style="dim")
            self._body.update(t)
            return

        line_index = 2
        for profile in aws_state.profiles:
            self._profile_lines.append((line_index, profile.name))
            self._append_profile_line(t, profile)
            line_index += 1

        t.append("\n")
        if aws_state.last_check_at:
            t.append(f"  checked: {aws_state.last_check_at[:19]}\n", style="dim")
        t.append("\n  click expired profiles to launch SSO login.\n", style="dim")
        t.append("  press a to close.", style="dim")
        self._body.update(t)

    def _append_profile_line(self, t: Text, profile: AwsProfile) -> None:
        """Render a single profile line with status color coding."""
        status_styles = {
            "active": "green",
            "expired": "yellow",
            "error": "red",
            "unknown": "dim",
        }
        style = status_styles.get(profile.status, "dim")

        t.append(f"  {profile.name:<14}", style="bold")
        t.append(f" {profile.status:<8}", style=style)
        if profile.region:
            t.append(f" {profile.region:<12}", style="dim")
        if profile.status in ("expired", "error"):
            t.append(" [login]", style="bold yellow")
        t.append("\n")

    def on_click(self, event: events.Click) -> None:
        if event.button == 3:
            self.close()
            return
        # Map click y to a profile line and request SSO login if needed
        click_y = event.y
        for line_idx, profile_name in self._profile_lines:
            if click_y == line_idx:
                self.post_message(self.SsoLoginRequested(profile_name))
                break
