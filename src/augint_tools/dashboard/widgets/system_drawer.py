"""SystemDrawer -- right-docked drawer showing local system info.

Displays CPU, RAM, GPU, Docker containers, and network connectivity.
Shares the right dock position with the existing Drawer widget via a
mode property that determines which content to show.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual import events
from textual.containers import Container
from textual.widgets import Static

if TYPE_CHECKING:
    from ..state import AppState


def _progress_bar(fraction: float, width: int) -> str:
    """Unicode-block progress bar, fraction clamped to [0, 1]."""
    frac = max(0.0, min(1.0, fraction))
    filled = int(round(frac * width))
    return "\u2588" * filled + "\u2591" * (width - filled)


class SystemDrawer(Container):
    """Right-docked drawer for system info. Overlays the card grid."""

    DEFAULT_CSS = """
    SystemDrawer {
        layer: overlay;
        dock: right;
        width: 48;
        height: 100%;
        offset: 48 0;
        padding: 1 2;
        transition: offset 180ms in_out_cubic;
    }
    SystemDrawer.open {
        offset: 0 0;
    }
    """

    def __init__(self, id: str = "system-drawer") -> None:
        super().__init__(id=id)
        self._body = Static("", id="system-drawer-body")
        self._docker_show_all: bool = True

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

    def toggle_docker_filter(self) -> None:
        """Toggle between showing all containers and only augint-shell ones."""
        self._docker_show_all = not self._docker_show_all

    def refresh_content(self, state: AppState) -> None:
        """Rebuild the drawer body from current state."""
        t = Text()
        t.append("System\n\n", style="bold")
        self._append_cpu(t, state)
        self._append_ram(t, state)
        self._append_gpu(t, state)
        self._append_docker(t, state)
        self._append_network(t, state)
        t.append("\npress D to close, click Docker to filter.", style="dim")
        self._body.update(t)

    def _append_cpu(self, t: Text, state: AppState) -> None:
        snap = state.system_snapshot
        if snap is None or snap.cpu is None:
            t.append("CPU  ", style="bold")
            t.append("no data\n\n", style="dim")
            return
        cpu = snap.cpu
        bar_width = 14
        frac = cpu.usage_pct / 100.0
        color = "green"
        if frac >= 0.90:
            color = "red"
        elif frac >= 0.75:
            color = "yellow"
        elif frac >= 0.60:
            color = "#d4a017"
        t.append("CPU  ", style="bold")
        t.append(_progress_bar(frac, bar_width), style=color)
        t.append(f" {cpu.usage_pct:.0f}%  {cpu.core_count} cores\n")
        t.append(
            f"load {cpu.load_avg_1m} / {cpu.load_avg_5m} / {cpu.load_avg_15m}\n\n",
            style="dim",
        )

    def _append_ram(self, t: Text, state: AppState) -> None:
        ram = state.ram_stats
        if ram is None:
            return
        bar_width = 14
        frac = ram.used_fraction
        color = "green"
        if frac >= 0.90:
            color = "red"
        elif frac >= 0.75:
            color = "yellow"
        elif frac >= 0.60:
            color = "#d4a017"
        t.append("RAM  ", style="bold")
        t.append(_progress_bar(frac, bar_width), style=color)
        pct = int(round(frac * 100))
        t.append(f" {pct}%  {ram.used_gb:.0f}/{ram.total_gb:.0f}G\n\n")

    def _append_gpu(self, t: Text, state: AppState) -> None:
        gpu = state.gpu_stats
        if gpu is None:
            return
        bar_width = 14
        t.append("GPU  ", style="bold")
        t.append(f"{gpu.name[:16]}")
        extras: list[str] = []
        if gpu.temp_c is not None:
            extras.append(f"{gpu.temp_c}C")
        if gpu.power_w is not None:
            extras.append(f"{gpu.power_w:.0f}W")
        if extras:
            t.append(" " + " ".join(extras), style="dim")
        t.append("\n")

        # Utilization bar
        util_color = "green"
        if gpu.util_pct >= 90:
            util_color = "red"
        elif gpu.util_pct >= 60:
            util_color = "#d4a017"
        t.append("util ", style="dim")
        t.append(_progress_bar(gpu.util_fraction, bar_width), style=util_color)
        t.append(f" {gpu.util_pct}%\n")

        # VRAM bar
        vram_frac = gpu.vram_fraction
        vram_color = "green"
        if vram_frac >= 0.90:
            vram_color = "red"
        elif vram_frac >= 0.75:
            vram_color = "yellow"
        elif vram_frac >= 0.60:
            vram_color = "#d4a017"
        vram_pct = int(round(vram_frac * 100))
        t.append("vram ", style="dim")
        t.append(_progress_bar(vram_frac, bar_width), style=vram_color)
        t.append(f" {vram_pct}%  {gpu.vram_used_gb:.0f}/{gpu.vram_total_gb:.0f}G\n\n")

    def _append_docker(self, t: Text, state: AppState) -> None:
        snap = state.system_snapshot
        if snap is None:
            t.append("Docker  ", style="bold")
            t.append("no data\n\n", style="dim")
            return
        docker = snap.docker
        if not docker.docker_available:
            t.append("Docker  ", style="bold")
            t.append("unavailable\n\n", style="dim")
            return

        total = len(docker.containers)
        augint_count = docker.augint_shell_count
        t.append("Docker  ", style="bold")
        t.append(f"{total} containers", style="dim")
        if augint_count:
            t.append(f" ({augint_count} augint-shell)", style="dim")
        t.append("\n")

        # Show containers based on filter
        containers = docker.containers
        if not self._docker_show_all:
            containers = tuple(c for c in containers if c.is_augint_shell)

        for c in containers[:12]:
            name_style = "bold" if c.is_augint_shell else ""
            status_style = "green" if c.status.lower() in ("running", "up") else "dim"
            t.append(f"  {c.name[:22]:<22} ", style=name_style)
            t.append(f"{c.status:<10}", style=status_style)
            t.append("\n")
        remaining = len(containers) - 12
        if remaining > 0:
            t.append(f"  (+{remaining} more)\n", style="dim")
        t.append("\n")

    def _append_network(self, t: Text, state: AppState) -> None:
        snap = state.system_snapshot
        if snap is None:
            t.append("Network  ", style="bold")
            t.append("no data\n\n", style="dim")
            return
        net = snap.network
        status_style = "green" if net.connected else "red"
        status_word = "connected" if net.connected else "disconnected"
        t.append("Network  ", style="bold")
        t.append(status_word, style=status_style)
        t.append("\n")
        if net.latency_ms is not None:
            t.append(f"  ping   {net.latency_ms:.0f}ms (1.1.1.1)\n", style="dim")
        if net.http_latency_ms is not None:
            t.append(f"  http   {net.http_latency_ms:.0f}ms (gstatic.com)\n", style="dim")
        t.append("\n")

    def on_click(self, event: events.Click) -> None:
        if event.button == 3:
            self.close()
            return
        # Check if the click is in the Docker section area -- toggle filter
        # Simple heuristic: look at the body text around the click offset
        # Use a message approach instead for simplicity
        body_text = self._body.renderable
        if isinstance(body_text, Text) and "Docker" in body_text.plain:
            # Check if click y is in docker section
            lines = body_text.plain.split("\n")
            docker_start = None
            for i, line in enumerate(lines):
                if line.startswith("Docker"):
                    docker_start = i
                    break
            if docker_start is not None:
                # Approximate: click within the docker section toggles filter
                # The y offset in the body corresponds to lines
                click_y = event.y
                if docker_start <= click_y <= docker_start + 15:
                    self.toggle_docker_filter()
