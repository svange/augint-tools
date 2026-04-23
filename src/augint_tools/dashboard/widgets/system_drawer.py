"""SystemDrawer -- bottom-docked drawer showing local system info.

Displays CPU, RAM, GPU, and Docker containers in a multi-column layout.
Docks to the bottom of the screen so it spans the full width and shows
more information at a glance than the old right-side panel.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual import events
from textual.containers import Container, Horizontal
from textual.widgets import Static

if TYPE_CHECKING:
    from ..state import AppState


def _progress_bar(fraction: float, width: int) -> str:
    """Unicode-block progress bar, fraction clamped to [0, 1]."""
    frac = max(0.0, min(1.0, fraction))
    filled = int(round(frac * width))
    return "\u2588" * filled + "\u2591" * (width - filled)


def _bar_color(fraction: float) -> str:
    """Return a color string based on a utilization fraction."""
    if fraction >= 0.90:
        return "red"
    if fraction >= 0.75:
        return "yellow"
    if fraction >= 0.60:
        return "#d4a017"
    return "green"


class SystemDrawer(Container):
    """Bottom-docked drawer for system info. Overlays the card grid."""

    DEFAULT_CSS = """
    SystemDrawer {
        layer: overlay;
        dock: bottom;
        width: 100%;
        height: 14;
        offset: 0 14;
        padding: 1 2;
        transition: offset 180ms in_out_cubic;
    }
    SystemDrawer.open {
        offset: 0 0;
    }
    SystemDrawer > #sys-columns {
        width: 100%;
        height: 100%;
    }
    SystemDrawer > #sys-columns > .sys-col {
        width: 1fr;
        height: 100%;
        padding: 0 1;
    }
    SystemDrawer > #sys-columns > #sys-col-divider,
    SystemDrawer > #sys-columns > #sys-col-divider-2 {
        width: 1;
        height: 100%;
        background: #2a2a36;
    }
    """

    def __init__(self, id: str = "system-drawer") -> None:
        super().__init__(id=id)
        self._col_left = Static("", id="sys-col-left", classes="sys-col")
        self._col_mid = Static("", id="sys-col-mid", classes="sys-col")
        self._col_right = Static("", id="sys-col-right", classes="sys-col")
        self._docker_show_all: bool = True

    def compose(self):
        yield Horizontal(
            self._col_left,
            Static("", id="sys-col-divider"),
            self._col_mid,
            Static("", id="sys-col-divider-2"),
            self._col_right,
            id="sys-columns",
        )

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
        """Rebuild all three columns from current state."""
        self._col_left.update(self._build_cpu_ram(state))
        self._col_mid.update(self._build_gpu(state))
        self._col_right.update(self._build_docker(state))

    # ---- column builders ----

    def _build_cpu_ram(self, state: AppState) -> Text:
        t = Text()
        t.append("CPU / RAM\n", style="bold")
        snap = state.system_snapshot
        bar_w = 16
        if snap is None or snap.cpu is None:
            t.append("CPU  ", style="bold")
            t.append("no data\n", style="dim")
        else:
            cpu = snap.cpu
            frac = cpu.usage_pct / 100.0
            t.append("CPU  ", style="bold")
            t.append(_progress_bar(frac, bar_w), style=_bar_color(frac))
            t.append(f" {cpu.usage_pct:.0f}%  {cpu.core_count}c\n")
            t.append(
                f"     load {cpu.load_avg_1m}/{cpu.load_avg_5m}/{cpu.load_avg_15m}\n",
                style="dim",
            )

        ram = state.ram_stats
        if ram is not None:
            frac = ram.used_fraction
            pct = int(round(frac * 100))
            t.append("RAM  ", style="bold")
            t.append(_progress_bar(frac, bar_w), style=_bar_color(frac))
            t.append(f" {pct}%  {ram.used_gb:.0f}/{ram.total_gb:.0f}G\n")

        # Network summary (kept brief; detailed DNS is in the network drawer)
        net = snap.network if snap else None
        if net is not None:
            t.append("\n")
            status_style = "green" if net.connected else "red"
            status_word = "connected" if net.connected else "disconnected"
            t.append("NET  ", style="bold")
            t.append(status_word, style=status_style)
            if net.latency_ms is not None:
                t.append(f"  {net.latency_ms:.0f}ms", style="dim")
            t.append("\n")

        t.append("\npress s to close", style="dim")
        return t

    def _build_gpu(self, state: AppState) -> Text:
        t = Text()
        t.append("GPU\n", style="bold")
        gpu = state.gpu_stats
        if gpu is None:
            t.append("no GPU detected\n", style="dim")
            return t
        bar_w = 16
        t.append(f"{gpu.name[:24]}", style="bold")
        extras: list[str] = []
        if gpu.temp_c is not None:
            extras.append(f"{gpu.temp_c}C")
        if gpu.power_w is not None:
            extras.append(f"{gpu.power_w:.0f}W")
        if extras:
            t.append("  " + " ".join(extras), style="dim")
        t.append("\n")

        t.append("util ", style="dim")
        util_color = _bar_color(gpu.util_pct / 100.0)
        t.append(_progress_bar(gpu.util_fraction, bar_w), style=util_color)
        t.append(f" {gpu.util_pct}%\n")

        vram_frac = gpu.vram_fraction
        vram_pct = int(round(vram_frac * 100))
        t.append("vram ", style="dim")
        t.append(_progress_bar(vram_frac, bar_w), style=_bar_color(vram_frac))
        t.append(f" {vram_pct}%  {gpu.vram_used_gb:.0f}/{gpu.vram_total_gb:.0f}G\n")
        return t

    def _build_docker(self, state: AppState) -> Text:
        t = Text()
        t.append("Docker\n", style="bold")
        snap = state.system_snapshot
        if snap is None:
            t.append("no data\n", style="dim")
            return t
        docker = snap.docker
        if not docker.docker_available:
            t.append("unavailable\n", style="dim")
            return t

        total = len(docker.containers)
        augint_count = docker.augint_shell_count
        t.append(f"{total} containers", style="dim")
        if augint_count:
            t.append(f" ({augint_count} augint-shell)", style="dim")
        t.append("  ")
        t.append("click to filter", style="dim italic")
        t.append("\n")

        containers = docker.containers
        if not self._docker_show_all:
            containers = tuple(c for c in containers if c.is_augint_shell)

        for c in containers[:10]:
            name_style = "bold" if c.is_augint_shell else ""
            status_style = "green" if c.status.lower() in ("running", "up") else "dim"
            t.append(f"  {c.name[:26]:<26} ", style=name_style)
            t.append(f"{c.status}\n", style=status_style)
        remaining = len(containers) - 10
        if remaining > 0:
            t.append(f"  (+{remaining} more)\n", style="dim")
        return t

    def on_click(self, event: events.Click) -> None:
        if event.button == 3:
            self.close()
            return
        # Click in the docker column toggles the container filter.
        docker_col = self._col_right
        if docker_col.region.contains(event.screen_x, event.screen_y):
            self.toggle_docker_filter()
