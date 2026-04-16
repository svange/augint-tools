"""Dashboard app shell -- DashboardApp + MainScreen.

Reactive app state is concentrated in :class:`AppState` (see ``state.py``);
the app translates user actions into state mutations and calls
``MainScreen.rerender()`` to refresh widgets.
"""

from __future__ import annotations

import asyncio
import webbrowser
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from rich.text import Text
from textual import events
from textual.app import App
from textual.binding import Binding
from textual.containers import Container
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Footer, Static

from ._data import RepoStatus, fetch_repo_status, save_cache
from .health import FetchContext, RepoHealth, run_health_checks
from .layouts import list_layouts
from .screens.drilldown import DrillDownScreen
from .screens.error_log import ErrorLogScreen
from .screens.help import HelpScreen
from .state import (
    PANEL_WIDTH_MAX,
    PANEL_WIDTH_MIN,
    PANEL_WIDTH_STEP,
    SORT_MODES,
    AppState,
    available_filter_modes,
    bootstrap_from_cache,
    ensure_selection,
    move_selection,
    remember_repo_teams,
    selected_health,
    team_accent,
    visible_healths,
)
from .sysmeter import probe_gpu, probe_ram
from .themes import get_theme, list_themes
from .usage import claude_daily_message_buckets, fetch_all_usage
from .widgets.card_container import CardContainer
from .widgets.drawer import Drawer
from .widgets.highlight_bar import HighlightBar
from .widgets.repo_card import RepoCard
from .widgets.status_bar import StatusBar
from .widgets.top_drawer import TopDrawer

if TYPE_CHECKING:
    from github.Repository import Repository


def _progress_bar(fraction: float, width: int) -> str:
    """Unicode-block progress bar, ``fraction`` clamped to [0, 1]."""
    frac = max(0.0, min(1.0, fraction))
    filled = int(round(frac * width))
    return "\u2588" * filled + "\u2591" * (width - filled)


# Unicode 'lower N eighth blocks' for sparkline rendering (U+2581..U+2588).
_SPARK_GLYPHS = " \u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"


def _sparkline(values: list[int]) -> str:
    """Render an N-char sparkline for ``values`` using block-height glyphs."""
    if not values:
        return ""
    peak = max(values)
    if peak <= 0:
        return "\u2581" * len(values)
    out: list[str] = []
    for v in values:
        idx = int(round((v / peak) * (len(_SPARK_GLYPHS) - 1)))
        idx = max(0, min(len(_SPARK_GLYPHS) - 1, idx))
        out.append(_SPARK_GLYPHS[idx])
    return "".join(out)


# How long a newly-broken (or newly-degraded-to-yellow) repo keeps flashing
# its border. Past this window the border stays solid. Kept deliberately long
# enough to catch a post-push glance without turning into a permanent flash.
FLASH_WINDOW_SECONDS = 12 * 60 * 60
# Period of the flash oscillation, in seconds. Even (on/off) — don't change
# this without also checking the motion isn't distracting on slow terminals.
_FLASH_TICK_SECONDS = 0.6


def _card_severity_class(health: RepoHealth | None) -> str | None:
    """Return the severity bucket RepoCard applies as a CSS class.

    Mirrors ``RepoCard._apply_severity_class`` so the app can reason about
    transitions between refreshes without importing widget internals.
    """
    if health is None:
        return None
    from .health import Severity as _Sev

    status = health.status
    if status.main_status == "failure" or status.dev_status == "failure":
        return "critical"
    worst = health.worst_severity
    if worst == _Sev.CRITICAL:
        return "critical"
    if worst in (_Sev.HIGH, _Sev.MEDIUM) or status.open_prs > 0:
        return "warning"
    return "ok"


def _usage_status_color(status: str, spec) -> str:
    """Map a UsageStats.status onto the active theme's severity palette."""
    from .health import Severity as _Sev

    mapping: dict[str, str] = {
        "ok": spec.severity_colors[_Sev.OK],
        "warning": spec.severity_colors[_Sev.MEDIUM],
        "critical": spec.severity_colors[_Sev.CRITICAL],
    }
    return mapping.get(status, "dim")


class OrgDrawerHeader(Container):
    """Top-docked title bar with a subtle right-aligned refresh countdown.

    Clicking anywhere on the bar posts :class:`Toggle`, which the main
    screen maps onto the same action as the ``i`` key (open/close the org
    drawer). We don't subclass ``Header`` because Textual's built-in
    header widget tall-mode click handling fights our needs, and we want
    to inject a tiny countdown label on the right.
    """

    DEFAULT_CSS = """
    OrgDrawerHeader {
        dock: top;
        height: 1;
        layout: horizontal;
        background: #1a1a22;
    }
    OrgDrawerHeader > #hdr-title {
        width: 1fr;
        content-align: center middle;
        text-style: bold;
    }
    OrgDrawerHeader > #hdr-countdown {
        width: auto;
        padding: 0 1;
        color: #8b8b9a;
    }
    """

    class Toggle(Message):
        """Emitted when the user clicks the header."""

    def __init__(self, title: str = "") -> None:
        super().__init__(id="org-drawer-header")
        self._initial_title = title
        self._title_widget = Static("", id="hdr-title")
        self._countdown_widget = Static("", id="hdr-countdown")

    def compose(self):
        yield self._title_widget
        yield self._countdown_widget

    def on_mount(self) -> None:
        # App title isn't available until mount; set it once here.
        title = self._initial_title or getattr(self.app, "title", "") or ""
        self._title_widget.update(title)

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.post_message(self.Toggle())

    def set_countdown(self, text: str) -> None:
        """Update the right-side countdown label."""
        self._countdown_widget.update(text)


class MainScreen(Screen[None]):
    """Primary screen: header, status bar, highlight bar, card grid, footer, drawer."""

    def __init__(self, state: AppState, org_name: str) -> None:
        super().__init__(id="main-screen")
        self._state = state
        self._org_name = org_name
        self._header = OrgDrawerHeader()
        self._status_bar = StatusBar()
        self._highlight_bar = HighlightBar()
        self._card_container = CardContainer()
        self._drawer = Drawer()
        self._top_drawer = TopDrawer()
        self._cards_by_name: dict[str, RepoCard] = {}

    def compose(self):
        yield self._header
        self._status_bar.bind_state(self._state, self._org_name)
        yield self._status_bar
        self._highlight_bar.bind_state(self._state)
        yield self._highlight_bar
        yield self._top_drawer
        yield Container(self._card_container)
        yield self._drawer
        yield Footer()

    def on_mount(self) -> None:
        self.rerender()

    # ---- public API called from DashboardApp ----

    def rerender(self) -> None:
        self._rebuild_cards()
        self._apply_layout()
        self._update_selection_styling()
        self._highlight_bar.rerender()
        self._status_bar.tick()
        self._header.set_countdown(self._countdown_text())
        if self._top_drawer.is_open:
            self._top_drawer.set_content(
                self._org_drawer_content(),
                self._org_drawer_middle_content(),
                self._org_drawer_right_content(),
            )

    def rerender_usage_only(self) -> None:
        """Update only the usage-derived UI (HighlightBar + open usage/org drawer)."""
        self._highlight_bar.rerender()
        if self._drawer.is_open and self._drawer.mode == "usage":
            self._drawer.set_content(self._usage_drawer_content())
        if self._top_drawer.is_open:
            # Org drawer embeds a usage block; refresh it in place.
            self._top_drawer.set_content(
                self._org_drawer_content(),
                self._org_drawer_middle_content(),
                self._org_drawer_right_content(),
            )

    def tick_status(self) -> None:
        self._status_bar.tick()
        self._header.set_countdown(self._countdown_text())

    def apply_flash_phase(self, phase: bool, *, window_seconds: int) -> None:
        """Propagate the global flash phase to each card."""
        for card in self._cards_by_name.values():
            card.apply_flash_phase(phase, window_seconds=window_seconds)

    def _countdown_text(self) -> str:
        """Format the right-header countdown -- short and quiet on purpose."""
        state = self._state
        if state.is_refreshing:
            return "refreshing..."
        if state.next_refresh_at is None:
            return "auto-refresh off"
        remaining = int((state.next_refresh_at - datetime.now(UTC)).total_seconds())
        if remaining <= 0:
            return "next: now"
        if remaining >= 60:
            m, s = divmod(remaining, 60)
            return f"next: {m}m{s:02d}s"
        return f"next: {remaining}s"

    def _rebuild_cards(self) -> None:
        theme_spec = get_theme(self._state.theme_name)
        vis = visible_healths(self._state)

        desired: dict[str, RepoHealth] = {h.status.full_name: h for h in vis}
        # Drop cards not in the visible set.
        for name in list(self._cards_by_name.keys()):
            if name not in desired:
                card = self._cards_by_name.pop(name)
                card.remove()
        # Add or update cards for the visible set, preserving order.
        known_teams = list(self._state.team_labels)
        cards_in_order: list[RepoCard] = []
        for full_name, health in desired.items():
            team_info = self._state.repo_teams.get(full_name)
            accent = team_accent(team_info.primary, known_teams) if team_info else "#808080"
            label = self._state.team_labels.get(team_info.primary, "") if team_info else ""
            existing = self._cards_by_name.get(full_name)
            if existing is None:
                card = RepoCard(
                    health=health,
                    theme_spec=theme_spec,
                    team_accent=accent,
                    team_label=label,
                )
                self._cards_by_name[full_name] = card
            else:
                card = existing
                # Only fire the reactive when the content has actually changed.
                # Identity differs across refreshes; compare by equality.
                if card.health != health:
                    card.health = health
                if card.team_accent != accent:
                    card.team_accent = accent
                if card.team_label != label:
                    card.team_label = label
                card.apply_theme(theme_spec)
            cards_in_order.append(card)

        # Re-mount cards in the correct order inside the container.
        self._card_container.set_cards(cards_in_order)

    def _apply_layout(self) -> None:
        try:
            available_width = self.size.width
        except Exception:
            available_width = 0
        self._card_container.apply_layout(
            self._state.layout_name, self._state, available_width=available_width
        )

    def _update_selection_styling(self) -> None:
        ensure_selection(self._state)
        for full_name, card in self._cards_by_name.items():
            card.selected = full_name == self._state.selected_full_name

    # ---- drawer ----

    def toggle_detail_drawer(self) -> None:
        health = selected_health(self._state)
        if health is None:
            return
        content = self._detail_drawer_content(health)
        self._drawer.toggle("detail", content)

    def toggle_usage_drawer(self) -> None:
        content = self._usage_drawer_content()
        self._drawer.toggle("usage", content)

    def toggle_org_drawer(self) -> None:
        left = self._org_drawer_content()
        middle = self._org_drawer_middle_content()
        right = self._org_drawer_right_content()
        self._top_drawer.toggle(left, middle, right)

    def on_org_drawer_header_toggle(self, _message: OrgDrawerHeader.Toggle) -> None:
        self.toggle_org_drawer()

    def _detail_drawer_content(self, health: RepoHealth) -> Text:
        status = health.status
        t = Text()
        t.append(f"{status.full_name}\n\n", style="bold")
        t.append(f"main ci: {status.main_status}\n")
        if status.is_service and status.dev_status:
            t.append(f"dev ci:  {status.dev_status}\n")
        t.append(f"issues:  {status.open_issues}\n")
        t.append(f"prs:     {status.open_prs} ({status.draft_prs} drafts)\n\n")
        if health.findings:
            t.append("findings:\n", style="bold")
            for finding in health.findings:
                t.append(f"  • {finding.check_name}: {finding.summary}\n")
        else:
            t.append("all checks green.\n", style="dim")
        t.append("\npress d to close, enter for full drilldown.", style="dim")
        return t

    def _org_drawer_content(self) -> Text:
        """Left column: system meters (GPU/RAM) + CI matrix."""
        state = self._state
        spec = get_theme(state.theme_name)
        t = Text()
        self._append_system_block(t, spec)
        if not state.healths:
            t.append("no data yet. press r to refresh.\n", style="dim")
            return t
        self._append_ci_matrix(t, state.healths, spec)
        return t

    def _org_drawer_middle_content(self) -> Text:
        """Middle column: org-wide stats, weather, activity, usage."""
        from .health import Severity as _Sev

        state = self._state
        spec = get_theme(state.theme_name)
        t = Text()

        if not state.healths:
            t.append(f"{self._org_name or 'Organization'} -- org dashboard\n\n", style="bold")
            t.append("no data yet. press r to refresh.\n\n", style="dim")
            self._append_usage_block(t, spec)
            return t

        by_sev: dict[_Sev, int] = {}
        for h in state.healths:
            by_sev[h.worst_severity] = by_sev.get(h.worst_severity, 0) + 1
        total = len(state.healths)
        ok_count = by_sev.get(_Sev.OK, 0)
        score = int((ok_count / total) * 100) if total else 0

        t.append(f"{self._org_name or 'Organization'} -- org dashboard", style="bold")
        t.append(f"    {total} repos  ·  {score}% green\n\n")

        self._append_severity_bar(t, by_sev, total, spec, width=40)

        t.append("repos    ", style="bold")
        self._append_repo_glyphs(t, spec)
        t.append("\n\n")

        self._append_weather(t, by_sev, state.healths, spec)
        self._append_activity_spark(t, spec)
        self._append_pr_ages(t, state.healths, spec)
        self._append_team_mix(t, state, spec)
        self._append_usage_block(t, spec)
        t.append("press i or click header to close.", style="dim")
        return t

    def _org_drawer_right_content(self) -> Text:
        """Right column: check failures, service/lib mix, score histogram,
        recent errors, worst-repos leaderboard."""
        state = self._state
        spec = get_theme(state.theme_name)
        t = Text()
        if not state.healths:
            t.append("--\n", style="dim")
            return t
        self._append_check_breakdown(t, state.healths, spec)
        self._append_service_lib(t, state.healths, spec)
        self._append_score_histogram(t, state.healths, spec)
        self._append_recent_errors(t, state, spec)
        self._append_leaderboard(t, state.healths, spec)
        return t

    # ------------------------------------------------------------------
    # Right-column widget helpers
    # ------------------------------------------------------------------

    def _append_ci_matrix(self, t: Text, healths: list, spec) -> None:
        """Per-repo CI status as coloured dots (dev + main if service)."""
        t.append("ci matrix\n", style="bold")
        shown = healths[:24]
        for h in shown:
            status = h.status
            name = status.name[:18]
            t.append(f"  {name:<18} ")
            if status.is_service and status.dev_status:
                t.append("\u25cf", style=self._ci_dot_style(status.dev_status, spec))
                t.append(" ")
            else:
                t.append("  ")
            t.append("\u25cf", style=self._ci_dot_style(status.main_status, spec))
            t.append("\n")
        if len(healths) > len(shown):
            t.append(f"  (+{len(healths) - len(shown)} more)\n", style="dim")
        t.append("\n")

    def _ci_dot_style(self, status: str | None, spec) -> str:
        mapping: dict[str, str] = {
            "success": str(spec.status_pass),
            "failure": str(spec.status_fail),
            "in_progress": str(spec.status_running),
        }
        return mapping.get(status or "", str(spec.status_unknown))

    def _append_check_breakdown(self, t: Text, healths: list, spec) -> None:
        """Tally of non-OK findings by check name."""
        from .health import Severity as _Sev

        tally: dict[str, int] = {}
        worst_sev: dict[str, _Sev] = {}
        for h in healths:
            for f in h.findings:
                if f.severity == _Sev.OK:
                    continue
                tally[f.check_name] = tally.get(f.check_name, 0) + 1
                prev = worst_sev.get(f.check_name, _Sev.OK)
                if int(f.severity) > int(prev):
                    worst_sev[f.check_name] = f.severity
        t.append("failing checks\n", style="bold")
        if not tally:
            t.append("  none\n\n", style="dim")
            return
        ordered = sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))
        total = sum(tally.values())
        bar_width = 16
        for name, count in ordered[:6]:
            label = name.replace("_", " ")[:18]
            colour = spec.severity_colors.get(
                worst_sev.get(name, _Sev.OK), spec.severity_colors[_Sev.OK]
            )
            blocks = max(1, int(round(count / total * bar_width)))
            t.append(f"  {label:<18} ")
            t.append("\u2588" * blocks, style=colour)
            t.append(f" {count}\n", style="dim")
        t.append("\n")

    def _append_service_lib(self, t: Text, healths: list, spec) -> None:
        """Split of services vs libraries and their health."""
        from .health import Severity as _Sev

        services = [h for h in healths if h.status.is_service]
        libs = [h for h in healths if not h.status.is_service]
        t.append("service / lib\n", style="bold")

        def _line(label: str, bucket: list) -> None:
            if not bucket:
                t.append(f"  {label:<9} 0\n", style="dim")
                return
            ok = sum(1 for h in bucket if h.worst_severity == _Sev.OK)
            bad = len(bucket) - ok
            t.append(f"  {label:<9} {len(bucket):>3}   ")
            if ok:
                t.append(f"{ok} ok", style=spec.severity_colors[_Sev.OK])
            if bad:
                if ok:
                    t.append(" · ")
                t.append(f"{bad} issues", style=spec.severity_colors[_Sev.HIGH])
            t.append("\n")

        _line("services", services)
        _line("libs", libs)
        t.append("\n")

    def _append_score_histogram(self, t: Text, healths: list, spec) -> None:
        """Health-score histogram in 10-point buckets."""
        from .health import Severity as _Sev

        if not healths:
            return
        buckets = [0] * 10  # 0-9, 10-19, ..., 90-100
        for h in healths:
            idx = min(9, max(0, h.score // 10))
            buckets[idx] += 1
        peak = max(buckets) or 1
        glyphs = " \u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
        t.append("score dist.\n", style="bold")
        t.append("  0 ", style="dim")
        for b in buckets:
            idx = int(round((b / peak) * (len(glyphs) - 1)))
            colour = (
                spec.severity_colors[_Sev.CRITICAL]
                if b and buckets.index(b) < 5
                else spec.severity_colors[_Sev.OK]
            )
            t.append(glyphs[idx], style=colour)
        t.append(" 100\n", style="dim")
        avg = sum(h.score for h in healths) / len(healths)
        t.append(f"  avg score {avg:.0f}\n\n", style="dim")

    def _append_recent_errors(self, t: Text, state, spec) -> None:
        """Last few timestamped errors from the error log."""
        t.append("recent errors\n", style="bold")
        if not state.errors:
            t.append("  none\n", style="dim")
            return
        for entry in state.errors[-3:]:
            ts = entry.timestamp.strftime("%H:%M:%S")
            msg = entry.message[:44]
            t.append(f"  {ts} ", style="dim")
            t.append(f"{entry.source}: ", style="bold red")
            t.append(f"{msg}\n")

    # ------------------------------------------------------------------
    # Widget helpers for the top drawer
    # ------------------------------------------------------------------

    def _append_weather(self, t: Text, by_sev: dict, healths: list, spec) -> None:
        """One-line 'weather' verdict summarising org health."""
        from .health import Severity as _Sev

        failing_main = sum(1 for h in healths if h.status.main_status == "failure")
        critical = by_sev.get(_Sev.CRITICAL, 0)
        high = by_sev.get(_Sev.HIGH, 0)
        if critical or failing_main >= 2:
            glyph, word, style = "[!!]", "stormy", spec.severity_colors[_Sev.CRITICAL]
        elif high or failing_main:
            glyph, word, style = "[~~]", "overcast", spec.severity_colors[_Sev.HIGH]
        elif by_sev.get(_Sev.MEDIUM, 0) or by_sev.get(_Sev.LOW, 0):
            glyph, word, style = "[--]", "partly cloudy", spec.severity_colors[_Sev.MEDIUM]
        else:
            glyph, word, style = "[**]", "sunny", spec.severity_colors[_Sev.OK]

        t.append("weather  ", style="bold")
        t.append(f"{glyph} {word}", style=f"bold {style}")
        bits: list[str] = []
        if failing_main:
            bits.append(f"{failing_main} failing CI")
        if critical:
            bits.append(f"{critical} critical")
        if high:
            bits.append(f"{high} high")
        if not bits:
            bits.append("all checks clear")
        t.append("   " + " · ".join(bits), style="dim")
        t.append("\n\n")

    def _append_activity_spark(self, t: Text, spec) -> None:
        """7-day Claude-message-per-day sparkline + totals."""
        try:
            buckets = claude_daily_message_buckets(7)
        except Exception:
            buckets = []
        t.append("activity ", style="bold")
        if not buckets or not any(buckets):
            t.append("no recent Claude activity", style="dim")
            t.append("\n\n")
            return
        bar = _sparkline(buckets)
        peak = max(buckets)
        total = sum(buckets)
        t.append(bar, style="cyan")
        t.append(f"  7d: {total:,} msgs   peak {peak:,}/day", style="dim")
        t.append("\n\n")

    def _append_pr_ages(self, t: Text, healths: list, spec) -> None:
        """Approximate PR-age histogram using the state's open_prs + stale findings."""
        from .health import Severity as _Sev

        # Exact age per PR is not in RepoStatus, but the stale-PR check flags
        # any PR past the threshold; we split total open PRs into "stale" (from
        # that finding) vs the rest, which we treat as "active".
        total_prs = sum(h.status.open_prs for h in healths)
        drafts = sum(h.status.draft_prs for h in healths)
        stale = 0
        for h in healths:
            for f in h.findings:
                if f.check_name == "stale_prs" and f.severity != _Sev.OK:
                    # Each stale finding carries the count in its summary; fall
                    # back to 1 so the bar still shows a presence.
                    try:
                        stale += int("".join(c for c in f.summary if c.isdigit()) or "0")
                    except ValueError:
                        stale += 1
        stale = min(stale, total_prs)
        active = max(0, total_prs - stale - drafts)

        t.append("pr ages  ", style="bold")
        if total_prs == 0:
            t.append("no open PRs across the org", style="dim")
            t.append("\n\n")
            return
        bar_width = 30

        def bar(n: int, total: int, colour: str) -> None:
            if total <= 0:
                return
            blocks = max(1, int(round(n / total * bar_width))) if n > 0 else 0
            if blocks > 0:
                t.append("\u2588" * blocks, style=colour)

        bar(active, total_prs, spec.severity_colors[_Sev.OK])
        bar(stale, total_prs, spec.severity_colors[_Sev.HIGH])
        bar(drafts, total_prs, "dim")
        t.append(f"  active {active}", style="dim")
        if stale:
            t.append(f"  stale {stale}", style=spec.severity_colors[_Sev.HIGH])
        if drafts:
            t.append(f"  draft {drafts}", style="dim")
        t.append("\n\n")

    def _append_team_mix(self, t: Text, state, spec) -> None:
        """Coloured bar of team-owned repos, widest team first."""
        if not state.repo_teams:
            return
        counts: dict[str, int] = {}
        for info in state.repo_teams.values():
            counts[info.primary] = counts.get(info.primary, 0) + 1
        if not counts:
            return
        total = sum(counts.values())
        ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        t.append("teams    ", style="bold")
        from .state import team_accent as _team_accent

        known_teams = list(state.team_labels)
        bar_width = 30
        used = 0
        for i, (key, count) in enumerate(ordered):
            slot = int(count / total * bar_width) if total else 0
            if i == len(ordered) - 1:
                slot = bar_width - used
            used += slot
            if slot > 0:
                t.append("\u2588" * slot, style=_team_accent(key, known_teams))
        t.append("  ")
        legend_bits: list[str] = []
        for key, count in ordered[:4]:
            label = state.team_labels.get(key, key)
            legend_bits.append(f"{label} {count}")
        if len(ordered) > 4:
            legend_bits.append(f"+{len(ordered) - 4} more")
        t.append(" · ".join(legend_bits), style="dim")
        t.append("\n\n")

    def _append_leaderboard(self, t: Text, healths: list, spec) -> None:
        """Worst-5 repos by score (lower = worse). Skip when everything green."""
        from .health import Severity as _Sev

        worst = [h for h in healths if h.worst_severity != _Sev.OK]
        if not worst:
            return
        worst.sort(key=lambda h: (h.score, int(h.worst_severity), h.status.name.lower()))
        t.append("worst 5  ", style="bold")
        t.append("\n")
        for rank, h in enumerate(worst[:5], start=1):
            colour = spec.severity_colors.get(h.worst_severity, spec.severity_colors[_Sev.OK])
            first = next((f for f in h.findings if f.severity != _Sev.OK), None)
            summary = first.summary if first else h.worst_severity.name.lower()
            t.append(f"  {rank}. ")
            t.append(f"{h.status.name:<22}", style="bold")
            t.append(f" {h.worst_severity.name.lower():<8}", style=colour)
            t.append(f" score {h.score:>3}   ")
            t.append(summary, style="dim")
            t.append("\n")
        t.append("\n")

    def _append_severity_bar(
        self,
        t: Text,
        by_sev: dict,
        total: int,
        spec,  # ThemeSpec
        *,
        width: int = 40,
    ) -> None:
        """Render a colour-segmented block bar representing severity distribution."""
        from .health import Severity as _Sev

        order = [
            (_Sev.CRITICAL, "crit"),
            (_Sev.HIGH, "high"),
            (_Sev.MEDIUM, "med"),
            (_Sev.LOW, "low"),
            (_Sev.OK, "ok"),
        ]
        t.append("health  [", style="bold")
        if total <= 0:
            t.append(" " * width + "]\n")
            return
        # Allocate whole-block slots proportionally; give the remainder to OK
        # so the bar width stays constant regardless of rounding.
        slots: dict = {}
        used = 0
        for sev, _ in order:
            count = by_sev.get(sev, 0)
            slot = int(count / total * width) if count else 0
            slots[sev] = slot
            used += slot
        if used < width:
            slots[_Sev.OK] = slots.get(_Sev.OK, 0) + (width - used)

        for sev, _ in order:
            n = slots.get(sev, 0)
            if n > 0:
                t.append("\u2588" * n, style=spec.severity_colors[sev])
        t.append("]   ")
        pieces: list[str] = []
        for sev, label in order:
            count = by_sev.get(sev, 0)
            if count:
                pieces.append(f"{label} {count}")
        t.append("  ".join(pieces) if pieces else "all green")
        t.append("\n\n")

    def _append_repo_glyphs(self, t: Text, spec) -> None:
        """One coloured dot per repo (worst severity), capped so we don't overflow."""
        from .health import Severity as _Sev

        repos = self._state.healths[:50]
        for h in repos:
            color = spec.severity_colors.get(h.worst_severity, spec.severity_colors[_Sev.OK])
            t.append("\u25cf", style=color)
            t.append(" ")
        if len(self._state.healths) > 50:
            t.append(f"  (+{len(self._state.healths) - 50} more)", style="dim")

    def _append_usage_block(self, t: Text, spec) -> None:
        """Usage meters for Claude Code / OpenAI / Copilot."""
        t.append("usage\n", style="bold")
        stats_list = self._state.usage_stats
        if not stats_list:
            t.append("  loading…\n", style="dim")
            return
        for stats in stats_list:
            t.append(f"  {stats.display_name:<13}")
            fraction = stats.usage_fraction
            if fraction is not None and stats.limit:
                bar = _progress_bar(fraction, 20)
                color = _usage_status_color(stats.status, spec)
                t.append(" ")
                t.append(bar, style=color)
                pct = int(fraction * 100)
                t.append(f"  {pct:>3}%   {stats.messages}/{stats.limit}")
                if stats.tier:
                    t.append(f"   {stats.tier}", style="dim")
            elif stats.status == "unconfigured":
                t.append(" —  ", style="dim")
                t.append(stats.error or "unconfigured", style="dim")
            elif stats.status == "empty":
                t.append(" —  ", style="dim")
                t.append("no data in window", style="dim")
                if stats.note:
                    t.append(f"  ({stats.note})", style="dim")
            elif stats.status == "unknown":
                t.append(" ?  ")
                if stats.tier:
                    t.append(f"{stats.tier}  ", style="dim")
                if stats.note:
                    t.append(stats.note, style="dim")
                elif stats.error:
                    t.append(stats.error, style="dim")
            else:
                t.append(f"  {stats.messages} msgs")
                if stats.tier:
                    t.append(f"   {stats.tier}", style="dim")
            t.append("\n")
        t.append("\n")

    def _append_system_block(self, t: Text, spec) -> None:
        """Host-system meters: RAM (always, when /proc/meminfo is readable)
        and GPU (only when nvidia-smi is present and returns data).

        Skipped silently when neither probe produced a result — e.g.,
        running the dashboard on a machine without an NVIDIA card and
        without ``/proc/meminfo`` (macOS / Windows without WSL).
        """
        from .health import Severity as _Sev

        ram = self._state.ram_stats
        gpu = self._state.gpu_stats
        if ram is None and gpu is None:
            return

        t.append("system\n", style="bold")
        bar_width = 20

        if ram is not None:
            used_frac = ram.used_fraction
            color = spec.severity_colors[_Sev.OK]
            if used_frac >= 0.90:
                color = spec.severity_colors[_Sev.CRITICAL]
            elif used_frac >= 0.75:
                color = spec.severity_colors[_Sev.HIGH]
            elif used_frac >= 0.60:
                color = spec.severity_colors[_Sev.MEDIUM]
            t.append("  RAM    ")
            t.append(_progress_bar(used_frac, bar_width), style=color)
            pct = int(round(used_frac * 100))
            t.append(f"  {ram.used_gb:.1f}/{ram.total_gb:.1f} GB ({pct}%)\n")

        if gpu is not None:
            t.append(f"  GPU    {gpu.name}")
            extras: list[str] = []
            if gpu.temp_c is not None:
                extras.append(f"{gpu.temp_c} C")
            if gpu.power_w is not None and gpu.power_limit_w is not None:
                extras.append(f"{gpu.power_w:.0f}/{gpu.power_limit_w:.0f} W")
            elif gpu.power_w is not None:
                extras.append(f"{gpu.power_w:.0f} W")
            if extras:
                t.append("   " + "  ".join(extras), style="dim")
            t.append("\n")

            util_color = spec.severity_colors[_Sev.OK]
            if gpu.util_pct >= 90:
                util_color = spec.severity_colors[_Sev.HIGH]
            elif gpu.util_pct >= 60:
                util_color = spec.severity_colors[_Sev.MEDIUM]
            t.append("  util   ")
            t.append(_progress_bar(gpu.util_fraction, bar_width), style=util_color)
            t.append(f"  {gpu.util_pct}%\n")

            vram_frac = gpu.vram_fraction
            vram_color = spec.severity_colors[_Sev.OK]
            if vram_frac >= 0.90:
                vram_color = spec.severity_colors[_Sev.CRITICAL]
            elif vram_frac >= 0.75:
                vram_color = spec.severity_colors[_Sev.HIGH]
            elif vram_frac >= 0.60:
                vram_color = spec.severity_colors[_Sev.MEDIUM]
            t.append("  vram   ")
            t.append(_progress_bar(vram_frac, bar_width), style=vram_color)
            vram_pct = int(round(vram_frac * 100))
            t.append(f"  {gpu.vram_used_gb:.1f}/{gpu.vram_total_gb:.1f} GB ({vram_pct}%)\n")
        t.append("\n")

    def _usage_drawer_content(self) -> Text:
        t = Text("usage (local data only)\n\n", style="bold")
        if not self._state.usage_stats:
            t.append("no usage data available.\n", style="dim")
            return t
        for stats in self._state.usage_stats:
            t.append(f"{stats.display_name}\n", style="bold")
            t.append(f"  status:   {stats.status}\n")
            if stats.tier:
                t.append(f"  tier:     {stats.tier}\n")
            t.append(f"  messages: {stats.messages}\n")
            t.append(f"  sessions: {stats.sessions}\n")
            if stats.note:
                t.append(f"  note:     {stats.note}\n", style="dim")
            if stats.error:
                t.append(f"  error:    {stats.error}\n", style="red")
            t.append("\n")
        t.append("press u to close.", style="dim")
        return t


class DashboardApp(App[None]):
    """V2 interactive health dashboard for GitHub repositories."""

    TITLE = "ai-gh dashboard"
    ANIMATION_LEVEL = "full"
    CSS = """
    Screen {
        layers: base overlay;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh_now", "Refresh"),
        Binding("s", "cycle_sort", "Sort"),
        Binding("f", "cycle_filter", "Filter"),
        Binding("g", "cycle_layout", "Layout"),
        Binding("t", "cycle_theme", "Theme"),
        Binding("b", "toggle_flash", "Blink"),
        Binding("d", "toggle_drawer", "Detail"),
        Binding("u", "toggle_usage", "Usage"),
        Binding("i", "toggle_org", "Org"),
        Binding("e", "open_errors", "Errors"),
        Binding("question_mark", "show_help", "Help"),
        Binding("plus", "widen_card", "Wider", show=False),
        Binding("equals_sign", "widen_card", show=False),
        Binding("minus", "narrow_card", "Narrower", show=False),
        Binding("enter", "open_selected", show=False, priority=True),
        Binding("o", "open_selected_browser", show=False, priority=True),
        Binding("down", "move_down", show=False, priority=True),
        Binding("j", "move_down", show=False, priority=True),
        Binding("up", "move_up", show=False, priority=True),
        Binding("k", "move_up", show=False, priority=True),
        Binding("left", "move_left", show=False, priority=True),
        Binding("h", "move_left", show=False, priority=True),
        Binding("right", "move_right", show=False, priority=True),
        Binding("l", "move_right", show=False, priority=True),
    ]

    def __init__(
        self,
        repos: list[Repository] | None = None,
        *,
        refresh_seconds: int = 600,
        initial_theme: str = "default",
        initial_layout: str = "packed",
        health_config: dict | None = None,
        org_name: str = "",
        skip_refresh: bool = False,
    ) -> None:
        super().__init__()
        self._repos = list(repos or [])
        self._refresh_seconds = refresh_seconds
        self._health_config = health_config or {}
        self._org_name = org_name
        self._skip_refresh = skip_refresh

        self.state = AppState()
        self.state.theme_name = initial_theme
        self.state.layout_name = initial_layout

        self._main: MainScreen | None = None
        # Flash-phase state for the "recently broken" border flash.
        # Cards in the flash window alternate class `card--flash-on` every
        # `_FLASH_TICK_SECONDS` so the border bounces between its severity
        # colour and a lighter shade defined in each theme's .tcss.
        self._flash_phase: bool = False
        self._flash_enabled: bool = True

    # ---- lifecycle ----

    def on_mount(self) -> None:
        self._load_theme_css(self.state.theme_name)
        # Cache-first: load before mounting so the first paint shows data.
        restrict = {r.full_name for r in self._repos} if self._repos else None
        bootstrap_from_cache(self.state, restrict_to=restrict)

        self._main = MainScreen(self.state, self._org_name)
        self.push_screen(self._main)

        # Usage fetch in the background -- never block the first paint.
        self._refresh_usage()

        if self._repos and not self._skip_refresh:
            # Seed the countdown before the first worker lands so the status
            # bar shows "next refresh in ..." from the first paint.
            self.state.next_refresh_at = datetime.now(UTC) + timedelta(
                seconds=self._refresh_seconds
            )
            self.set_interval(self._refresh_seconds, self._trigger_refresh)
            self._trigger_refresh()

        self.set_interval(1.0, self._tick_status)
        self.set_interval(60.0, self._refresh_usage)
        self.set_interval(_FLASH_TICK_SECONDS, self._tick_flash)
        # Probe host RAM / GPU in the background: once at startup so the
        # first org-drawer open already shows numbers, then every 3s while
        # the drawer is visible (see ``_tick_sysmeter``).
        self._refresh_sysmeter()
        self.set_interval(3.0, self._tick_sysmeter)

    async def action_quit(self) -> None:
        self.state.cancel_requested = True
        try:
            self.workers.cancel_all()
            try:
                await asyncio.wait_for(self.workers.wait_for_complete(), timeout=1.0)
            except (TimeoutError, Exception):
                pass
        except Exception:
            pass
        self.exit()

    # ---- refresh workers ----

    def _trigger_refresh(self) -> None:
        self.state.is_refreshing = True
        self.state.next_refresh_at = datetime.now(UTC) + timedelta(seconds=self._refresh_seconds)
        self.run_worker(self._do_refresh_sync, thread=True, exit_on_error=False)

    def _do_refresh_sync(self) -> None:
        try:
            self._do_refresh_inner()
            self.state.consecutive_errors = 0
            self.state.last_error_message = None
        except Exception as exc:
            self.state.is_refreshing = False
            self.state.consecutive_errors += 1
            msg = f"{exc.__class__.__name__}: {exc}"
            self.state.last_error_message = msg
            self.state.log_error("refresh", msg)
            self.call_from_thread(
                self.notify,
                f"Refresh failed: {exc.__class__.__name__}",
                severity="warning",
                timeout=5,
            )
            self.call_from_thread(self._rerender)

    def _do_refresh_inner(self) -> None:
        statuses: list[RepoStatus] = []
        for repo in self._repos:
            if self.state.cancel_requested:
                return
            remember_repo_teams(self.state, repo)
            try:
                statuses.append(fetch_repo_status(repo))
            except Exception as exc:
                self.state.log_error(
                    "refresh",
                    f"{repo.full_name}: {exc.__class__.__name__}: {exc}",
                )
                statuses.append(
                    RepoStatus(
                        name=getattr(repo, "name", "?"),
                        full_name=getattr(repo, "full_name", "?"),
                        is_service=False,
                        main_status="unknown",
                        main_error=None,
                        dev_status=None,
                        dev_error=None,
                        open_issues=0,
                        open_prs=0,
                        draft_prs=0,
                    )
                )

        healths: list[RepoHealth] = []
        for repo, status in zip(self._repos, statuses, strict=True):
            if self.state.cancel_requested:
                return
            try:
                ctx = FetchContext.build(repo)
                healths.append(
                    run_health_checks(repo, status, config=self._health_config, context=ctx)
                )
            except Exception as exc:
                self.state.log_error(
                    "refresh",
                    f"{status.full_name} health: {exc.__class__.__name__}: {exc}",
                )
                healths.append(RepoHealth(status=status))

        try:
            save_cache(statuses, healths=healths)
        except Exception as exc:
            self.state.log_error("cache", f"save failed: {exc.__class__.__name__}: {exc}")

        # Commit the new state on the main thread so action handlers don't
        # observe healths and health_by_name in a half-updated state.
        self.call_from_thread(self._commit_refresh, healths)

    def _commit_refresh(self, healths: list[RepoHealth]) -> None:
        # Carry forward / stamp warning-transition timestamps *before* we swap
        # the healths list, so the card-flash logic can tell whether yellow is
        # new (ok -> warning) or established. Timestamps on the RepoStatus side
        # (main_failing_since / dev_failing_since) come straight from GitHub.
        self._update_warning_since(healths)
        self.state.healths = healths
        self.state.health_by_name = {h.status.full_name: h for h in healths}
        self.state.last_refresh_at = datetime.now(UTC)
        self.state.is_refreshing = False
        self._rerender()

    def _update_warning_since(self, healths: list[RepoHealth]) -> None:
        """Stamp ``warning_since`` on the incoming healths.

        Carry the prior value when the severity class is unchanged; set a
        fresh timestamp on a green -> yellow transition; clear it when the
        card is green or has escalated to critical (critical is always-flash
        via the main/dev failing-since timestamps instead).
        """
        prior = self.state.health_by_name
        now_iso = datetime.now(UTC).isoformat()
        for h in healths:
            prev = prior.get(h.status.full_name)
            prev_class = _card_severity_class(prev) if prev is not None else None
            new_class = _card_severity_class(h)
            if new_class == "warning":
                if prev_class == "warning" and prev is not None and prev.warning_since:
                    h.warning_since = prev.warning_since
                else:
                    h.warning_since = now_iso
            else:
                h.warning_since = None

    def _refresh_usage(self) -> None:
        """Periodic usage refresh -- only repaints the usage UI, not the grid."""
        self.run_worker(self._refresh_usage_sync, thread=True, exit_on_error=False)

    def _refresh_usage_sync(self) -> None:
        try:
            stats = fetch_all_usage()
        except Exception as exc:
            self.state.log_error("usage", f"{exc.__class__.__name__}: {exc}")
            return
        self.state.usage_stats = stats
        self.call_from_thread(self._rerender_usage_only)

    def _tick_sysmeter(self) -> None:
        """Re-probe host system meters only while the org drawer is open.

        Skipping when the drawer is closed keeps us off the scheduler for
        the ~99% of the session where nothing would be drawn anyway.
        """
        if self._main is None:
            return
        if not self._main._top_drawer.is_open:
            return
        self._refresh_sysmeter()

    def _refresh_sysmeter(self) -> None:
        """Kick a background probe of GPU + RAM."""
        self.run_worker(self._refresh_sysmeter_sync, thread=True, exit_on_error=False)

    def _refresh_sysmeter_sync(self) -> None:
        try:
            gpu = probe_gpu()
            ram = probe_ram()
        except Exception as exc:
            self.state.log_error("sysmeter", f"{exc.__class__.__name__}: {exc}")
            return
        self.state.gpu_stats = gpu
        self.state.ram_stats = ram
        self.call_from_thread(self._rerender_usage_only)

    # ---- render glue ----

    def _rerender(self) -> None:
        if self._main is not None:
            try:
                self._main.rerender()
            except Exception as exc:
                self.state.log_error("ui", f"{exc.__class__.__name__}: {exc}")

    def _rerender_usage_only(self) -> None:
        if self._main is not None:
            try:
                self._main.rerender_usage_only()
            except Exception as exc:
                self.state.log_error("ui", f"{exc.__class__.__name__}: {exc}")

    def _tick_status(self) -> None:
        if self._main is not None:
            self._main.tick_status()

    def _tick_flash(self) -> None:
        """Advance the flash phase and push it to every visible card.

        Cheap: only toggles a CSS class, no widget rebuild. Cards decide
        themselves whether they're currently in the flash window.
        """
        self._flash_phase = not self._flash_phase
        if self._main is None:
            return
        phase = self._flash_phase if self._flash_enabled else False
        self._main.apply_flash_phase(phase, window_seconds=FLASH_WINDOW_SECONDS)

    # ---- actions ----

    def action_refresh_now(self) -> None:
        if not self._repos:
            self.notify("no repos configured", severity="warning", timeout=3)
            return
        self.notify("refreshing...", timeout=2)
        self._trigger_refresh()

    def action_cycle_sort(self) -> None:
        idx = SORT_MODES.index(self.state.sort_mode) if self.state.sort_mode in SORT_MODES else 0
        self.state.sort_mode = SORT_MODES[(idx + 1) % len(SORT_MODES)]
        self._rerender()
        self.notify(f"sort: {self.state.sort_mode}", timeout=2)

    def action_cycle_filter(self) -> None:
        from .widgets.status_bar import describe_filter

        modes = available_filter_modes(self.state.team_labels, self.state.repo_teams)
        try:
            idx = modes.index(self.state.filter_mode)
        except ValueError:
            idx = -1
        self.state.filter_mode = modes[(idx + 1) % len(modes)]
        self._rerender()
        label = describe_filter(self.state.filter_mode, self.state.team_labels)
        count = len(visible_healths(self.state))
        self.notify(f"filter: {label} -- {count} repos", timeout=2)

    def action_cycle_layout(self) -> None:
        layouts = list_layouts()
        try:
            idx = layouts.index(self.state.layout_name)
        except ValueError:
            idx = -1
        self.state.layout_name = layouts[(idx + 1) % len(layouts)]
        self._rerender()
        self.notify(f"layout: {self.state.layout_name}", timeout=2)

    def action_cycle_theme(self) -> None:
        themes = list_themes()
        try:
            idx = themes.index(self.state.theme_name)
        except ValueError:
            idx = -1
        new_theme = themes[(idx + 1) % len(themes)]
        self.state.theme_name = new_theme
        self._load_theme_css(new_theme)
        self._rerender()
        self.notify(f"theme: {new_theme}", timeout=2)

    def action_toggle_flash(self) -> None:
        """Enable or disable the recently-broken border flash."""
        self._flash_enabled = not self._flash_enabled
        if not self._flash_enabled and self._main is not None:
            # Clear the flash phase class immediately so nothing stays lit.
            self._main.apply_flash_phase(False, window_seconds=FLASH_WINDOW_SECONDS)
        state = "on" if self._flash_enabled else "off"
        self.notify(f"flash: {state}", timeout=2)

    def action_toggle_drawer(self) -> None:
        if self._main is not None:
            self._main.toggle_detail_drawer()

    def action_toggle_usage(self) -> None:
        if self._main is not None:
            self._main.toggle_usage_drawer()

    def action_toggle_org(self) -> None:
        if self._main is None:
            return
        self._main.toggle_org_drawer()
        # Kick a fresh probe on open so the sysmeter block isn't stale; the
        # periodic tick takes over from here.
        if self._main._top_drawer.is_open:
            self._refresh_sysmeter()

    def action_widen_card(self) -> None:
        self._resize_card(+PANEL_WIDTH_STEP)

    def action_narrow_card(self) -> None:
        self._resize_card(-PANEL_WIDTH_STEP)

    def _resize_card(self, delta: int) -> None:
        new_width = max(PANEL_WIDTH_MIN, min(PANEL_WIDTH_MAX, self.state.panel_width + delta))
        if new_width == self.state.panel_width:
            return
        self.state.panel_width = new_width
        # Layout-only reflow -- do not rebuild card widgets.
        if self._main is not None:
            self._main._apply_layout()

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        if event.ctrl:
            self._resize_card(+PANEL_WIDTH_STEP)
            event.stop()
            event.prevent_default()

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        if event.ctrl:
            self._resize_card(-PANEL_WIDTH_STEP)
            event.stop()
            event.prevent_default()

    def action_open_errors(self) -> None:
        self.push_screen(ErrorLogScreen(self.state))

    def action_show_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_move_down(self) -> None:
        move_selection(self.state, self._grid_step())
        self._rerender()

    def action_move_up(self) -> None:
        move_selection(self.state, -self._grid_step())
        self._rerender()

    def action_move_left(self) -> None:
        move_selection(self.state, -1)
        self._rerender()

    def action_move_right(self) -> None:
        move_selection(self.state, 1)
        self._rerender()

    def action_open_selected(self) -> None:
        health = selected_health(self.state)
        if health is not None:
            self.push_screen(DrillDownScreen(health))

    def action_open_selected_browser(self) -> None:
        health = selected_health(self.state)
        if health is not None:
            webbrowser.open(f"https://github.com/{health.status.full_name}")

    # ---- message handlers ----

    def on_repo_card_selected(self, message: RepoCard.Selected) -> None:
        self.state.selected_full_name = message.full_name
        self._rerender()

    def on_repo_card_drilldown_requested(self, message: RepoCard.DrilldownRequested) -> None:
        health = self.state.health_by_name.get(message.full_name)
        if health is not None:
            self.push_screen(DrillDownScreen(health))

    def on_repo_card_actions_requested(self, message: RepoCard.ActionsRequested) -> None:
        webbrowser.open_new_tab(f"https://github.com/{message.full_name}/actions")

    def on_repo_card_pulls_requested(self, message: RepoCard.PullsRequested) -> None:
        webbrowser.open_new_tab(f"https://github.com/{message.full_name}/pulls")

    def on_repo_card_go_back(self, _message: RepoCard.GoBack) -> None:
        if self._main is not None:
            if self._main._top_drawer.is_open:
                self._main._top_drawer.close()
                return
            if self._main._drawer.is_open:
                self._main._drawer.close()
                return
        if len(self.screen_stack) > 1:
            self.pop_screen()

    # ---- theme CSS loading ----

    def _load_theme_css(self, name: str) -> None:
        """Load the .tcss file for ``name`` into the app stylesheet.

        Replaces the previously-loaded theme (if any) so switching themes
        fully re-skins the UI without accumulating stale rules. In
        Textual 1.0 ``Stylesheet.source`` is keyed by ``(path, class_var)``
        tuples; the pop loop below must match that shape or old theme
        rules pile up and later-parsed rules win regardless of which
        theme the user selected.
        """
        try:
            path = str(get_theme(name).css_path)
            sheet = self.stylesheet  # type: ignore[has-type]
            all_theme_paths = {str(get_theme(n).css_path) for n in list_themes()}
            try:
                source = getattr(sheet, "source", None)
                if isinstance(source, dict):
                    for k in list(source):
                        key_path = k[0] if isinstance(k, tuple) and k else k
                        if (
                            isinstance(key_path, str)
                            and key_path in all_theme_paths
                            and key_path != path
                        ):
                            source.pop(k, None)
                    # Invalidate the cached rules_map so parse() rebuilds.
                    try:
                        sheet._rules_map = None
                    except Exception:
                        pass
            except Exception:
                pass
            sheet.read(path)
            sheet.parse()
            self.stylesheet = sheet
            for screen in list(self.screen_stack):
                try:
                    sheet.update(screen)
                except Exception:
                    pass
            sheet.update(self)
        except Exception as exc:
            self.state.log_error("ui", f"css load: {exc.__class__.__name__}: {exc}")

    # ---- helpers ----

    def _grid_step(self) -> int:
        """Rough column count for up/down navigation."""
        try:
            return max(1, self.size.width // max(10, self.state.panel_width + 2))
        except Exception:
            return 1


def run_dashboard(
    repos: list[Repository],
    *,
    refresh_seconds: int = 600,
    theme: str = "default",
    layout: str = "packed",
    health_config: dict | None = None,
    org_name: str = "",
    skip_refresh: bool = False,
) -> None:
    """Launch the v2 interactive dashboard."""
    app = DashboardApp(
        repos=repos,
        refresh_seconds=refresh_seconds,
        initial_theme=theme,
        initial_layout=layout,
        health_config=health_config,
        org_name=org_name,
        skip_refresh=skip_refresh,
    )
    app.run()
