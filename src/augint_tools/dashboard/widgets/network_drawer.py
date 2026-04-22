"""NetworkDrawer -- right-docked drawer showing DNS resolution status.

Displays DNS check results for all hostnames referenced in the
deployments.yaml. Failed resolutions are shown with HIGH severity
styling and an error message.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual import events
from textual.containers import Container
from textual.widgets import Static

if TYPE_CHECKING:
    from ..state import AppState


class NetworkDrawer(Container):
    """Right-docked drawer for DNS/network health. Overlays the card grid."""

    DEFAULT_CSS = """
    NetworkDrawer {
        layer: overlay;
        dock: right;
        width: 48;
        height: 100%;
        offset: 48 0;
        padding: 1 2;
        transition: offset 180ms in_out_cubic;
    }
    NetworkDrawer.open {
        offset: 0 0;
    }
    """

    def __init__(self, id: str = "network-drawer") -> None:
        super().__init__(id=id)
        self._body = Static("", id="network-drawer-body")

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

    def refresh_content(self, state: AppState) -> None:
        """Rebuild the drawer body from current state."""
        t = Text()
        t.append("Network / DNS\n\n", style="bold")

        # Ping section
        self._append_ping(t, state)

        # DNS section
        self._append_dns(t, state)

        t.append("\npress n to close.", style="dim")
        self._body.update(t)

    def _append_ping(self, t: Text, state: AppState) -> None:
        ping = state.ping_result
        if ping is None:
            t.append("Ping  ", style="bold")
            t.append("no data\n\n", style="dim")
            return
        status_style = "green" if ping.connected else "red"
        status_word = "connected" if ping.connected else "OFFLINE"
        t.append("Ping  ", style="bold")
        t.append(status_word, style=status_style)
        if ping.latency_ms is not None:
            t.append(f"  {ping.latency_ms:.0f}ms", style="dim")
        t.append("\n")
        t.append("  target: 1.1.1.1:443 (Cloudflare)\n", style="dim")
        t.append("\n")

    def _append_dns(self, t: Text, state: AppState) -> None:
        results = state.dns_results
        if not results:
            t.append("DNS  ", style="bold")
            t.append("no deployment URLs configured\n", style="dim")
            t.append("  add URLs via f (manage deployments)\n\n", style="dim")
            return

        failed = [r for r in results if not r.resolved]
        ok = [r for r in results if r.resolved]

        t.append("DNS  ", style="bold")
        t.append(f"{len(ok)} ok", style="green")
        if failed:
            t.append(f"  {len(failed)} FAILED", style="bold red")
        t.append("\n\n")

        # Show failures first (high severity)
        if failed:
            t.append("  FAILURES\n", style="bold red")
            for r in failed:
                t.append(f"  {r.hostname}\n", style="bold red")
                if r.error:
                    # Truncate long error messages
                    err = r.error if len(r.error) <= 38 else r.error[:35] + "..."
                    t.append(f"    {err}\n", style="red")
                if r.repos:
                    repos_str = ", ".join(r.repos[:3])
                    if len(r.repos) > 3:
                        repos_str += f" +{len(r.repos) - 3}"
                    t.append(f"    used by: {repos_str}\n", style="dim")
            t.append("\n")

        # Show resolved hosts
        if ok:
            t.append("  RESOLVED\n", style="dim")
            for r in ok:
                latency_str = f"{r.latency_ms:.0f}ms" if r.latency_ms is not None else "--"
                t.append(f"  {r.hostname:<30} ", style="")
                t.append(f"{latency_str}\n", style="dim")
            t.append("\n")

    def on_click(self, event: events.Click) -> None:
        if event.button == 3:
            self.close()
