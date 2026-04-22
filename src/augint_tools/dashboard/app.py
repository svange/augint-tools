"""Dashboard app shell -- DashboardApp + MainScreen.

Reactive app state is concentrated in :class:`AppState` (see ``state.py``);
the app translates user actions into state mutations and calls
``MainScreen.rerender()`` to refresh widgets.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from loguru import logger
from rich.text import Text
from textual import events
from textual.app import App
from textual.binding import Binding
from textual.containers import Container
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Static

from ._data import RepoStatus, build_status_from_snapshot, fetch_failing_run_detail, save_cache
from ._gql import (
    fetch_workspace_snapshot,
    fetch_workspace_teams,
    pick_package_json,
    pick_pipeline_yaml,
    pick_precommit,
    pick_pyproject,
    pick_renovate_config,
)
from ._helpers import get_viewer_login, list_repos_multi, list_user_orgs, strip_dotfile_repos
from .health import FetchContext, RepoHealth, run_health_checks
from .layouts import list_layouts
from .prefs import DashboardPrefs, save_prefs
from .screens.drilldown import DrillDownScreen
from .screens.filter_panel import FilterPanel
from .screens.help import HelpScreen
from .screens.org_manager import OrgManager
from .screens.repo_manager import RepoManager
from .screens.widget_help import WidgetHelpScreen
from .state import (
    PANEL_WIDTH_MAX,
    PANEL_WIDTH_MIN,
    PANEL_WIDTH_STEP,
    SORT_MODES,
    AppState,
    apply_open_source_team,
    bootstrap_from_cache,
    ensure_selection,
    merge_teams_snapshot,
    move_selection,
    selected_health,
    team_accent,
    visible_healths,
)
from .sysmeter import probe_gpu, probe_ram
from .sysprobe import probe_dns, probe_ping, probe_system
from .themes import get_theme, list_themes
from .widgets.aws_drawer import AwsDrawer
from .widgets.card_container import CardContainer
from .widgets.dashboard_footer import DashboardFooter
from .widgets.drawer import Drawer
from .widgets.effect_sprite import SPRITE_WIDTH, EffectKind, EffectSprite
from .widgets.error_drawer import ErrorDrawer
from .widgets.highlight_bar import HighlightBar
from .widgets.network_drawer import NetworkDrawer
from .widgets.repo_card import RepoCard
from .widgets.status_bar import StatusBar, format_header_refresh_line
from .widgets.system_drawer import SystemDrawer
from .widgets.top_drawer import TopDrawer

if TYPE_CHECKING:
    from github import Github
    from github.Repository import Repository

    from ._gql import TeamsSnapshot


def _progress_bar(fraction: float, width: int) -> str:
    """Unicode-block progress bar, ``fraction`` clamped to [0, 1]."""
    frac = max(0.0, min(1.0, fraction))
    filled = int(round(frac * width))
    return "\u2588" * filled + "\u2591" * (width - filled)


def _concat_sections(sections: list[tuple[str, Text]]) -> Text:
    """Concatenate a list of named sections into a single ``Text``.

    Used to keep the original single-``Text`` drawer accessors working
    after the drawer column renderers were split into per-section chunks.
    Section ids are discarded -- they only matter for click routing.
    """
    out = Text()
    for _, chunk in sections:
        out.append_text(chunk)
    return out


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
    if worst in (_Sev.HIGH, _Sev.MEDIUM):
        return "warning"
    return "ok"


class OrgDrawerHeader(Container):
    """Top-docked two-line header: title + prominent refresh status.

    Clicking anywhere on the bar posts :class:`Toggle`, which the main
    screen maps onto the same action as the ``i`` key (open/close the org
    drawer). Line 1 holds the app title. Line 2 shows the time of the
    last refresh, a live countdown to the next auto-refresh, and the
    configured interval -- making "yes, refreshes are happening" an
    unmissable, always-visible fact.
    """

    DEFAULT_CSS = """
    OrgDrawerHeader {
        dock: top;
        height: 2;
        layout: vertical;
        background: #1a1a22;
        overflow: hidden;
    }
    OrgDrawerHeader > #hdr-title {
        width: 100%;
        height: 1;
        content-align: center middle;
        text-style: bold;
        overflow: hidden;
    }
    OrgDrawerHeader > #hdr-refresh {
        width: 100%;
        height: 1;
        content-align: center middle;
        color: #c8c8d2;
        overflow: hidden;
    }
    """

    class Toggle(Message):
        """Emitted when the user clicks the header."""

    def __init__(self, title: str = "") -> None:
        super().__init__(id="org-drawer-header")
        self._initial_title = title
        self._title_widget = Static("", id="hdr-title")
        self._refresh_widget = Static("", id="hdr-refresh")

    def compose(self):
        yield self._title_widget
        yield self._refresh_widget

    def on_mount(self) -> None:
        # App title isn't available until mount; set it once here.
        title = self._initial_title or getattr(self.app, "title", "") or ""
        self._title_widget.update(title)

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.post_message(self.Toggle())

    def set_refresh_line(self, text: str) -> None:
        """Update the second-line refresh status text."""
        self._refresh_widget.update(text)


class MainScreen(Screen[None]):
    """Primary screen: header, status bar, highlight bar, card grid, footer, drawer."""

    def __init__(
        self,
        state: AppState,
        org_name: str = "",
        owners: list[str] | None = None,
        *,
        refresh_seconds: int = 0,
    ) -> None:
        super().__init__(id="main-screen")
        self._state = state
        self._org_name = org_name
        self._owners: list[str] = owners or ([org_name] if org_name else [])
        self._refresh_seconds = refresh_seconds
        self._header = OrgDrawerHeader()
        self._status_bar = StatusBar()
        self._highlight_bar = HighlightBar()
        self._card_container = CardContainer()
        self._drawer = Drawer()
        self._top_drawer = TopDrawer()
        self._error_drawer = ErrorDrawer()
        self._system_drawer = SystemDrawer()
        self._network_drawer = NetworkDrawer()
        self._aws_drawer = AwsDrawer()
        self._cards_by_name: dict[str, RepoCard] = {}
        # Active effect sprites keyed by repo full_name. Persistent until
        # the user clicks anywhere; positioned over the matching card's
        # top-right corner via screen-relative offset.
        self._effects_by_name: dict[str, EffectSprite] = {}

    def compose(self):
        yield self._header
        self._status_bar.bind_state(self._state, self._org_name, owners=self._owners)
        yield self._status_bar
        self._highlight_bar.bind_state(self._state, refresh_seconds=self._refresh_seconds)
        yield self._highlight_bar
        yield self._top_drawer
        yield Container(self._card_container)
        yield self._aws_drawer
        yield self._drawer
        yield self._system_drawer
        yield self._network_drawer
        yield self._error_drawer
        yield DashboardFooter()

    def on_mount(self) -> None:
        self.rerender()

    def on_resize(self, _event: events.Resize) -> None:
        # Terminal resize reflows the card grid; keep sprites snapped.
        if self._effects_by_name:
            self.call_after_refresh(self._reposition_all_effects)

    # ---- public API called from DashboardApp ----

    def rerender(self) -> None:
        self._rebuild_cards()
        self._apply_layout()
        self._update_selection_styling()
        self._highlight_bar.rerender()
        self._status_bar.tick()
        self._header.set_refresh_line(self._refresh_line_text())
        if self._top_drawer.is_open:
            self._top_drawer.set_content(
                self._org_drawer_left_sections(),
                self._org_drawer_middle_sections(),
                self._org_drawer_right_sections(),
            )
        # Card regions can change between rerenders (filter, sort, layout
        # toggle) so re-anchor any persistent sprites. call_after_refresh
        # so we read regions after the layout pass has actually run.
        if self._effects_by_name:
            self.call_after_refresh(self._reposition_all_effects)

    def rerender_org_drawer(self) -> None:
        """Refresh the org drawer system meters when probes update."""
        if self._top_drawer.is_open:
            self._top_drawer.set_content(
                self._org_drawer_left_sections(),
                self._org_drawer_middle_sections(),
                self._org_drawer_right_sections(),
            )

    def tick_status(self) -> None:
        self._status_bar.tick()
        self._header.set_refresh_line(self._refresh_line_text())
        # Drive the HighlightBar's second-line countdown every second so
        # the user sees a live "next: in Xs" tick down -- proof that the
        # refresh loop is running even when the data itself hasn't changed.
        self._highlight_bar.rerender()

    def apply_flash_phase(self, phase: bool, *, window_seconds: int) -> None:
        """Propagate the global flash phase to each card."""
        for card in self._cards_by_name.values():
            card.apply_flash_phase(phase, window_seconds=window_seconds)

    def spawn_effect(self, full_name: str, kind: EffectKind) -> None:
        """Mount (or replace) a pixel-art sprite for ``full_name``.

        Sprites are layered above the card grid via ``layer: effects`` and
        anchored to the card's top-right corner. Replacing an existing sprite
        avoids stacking when a card flips back-and-forth between states.
        """
        existing = self._effects_by_name.pop(full_name, None)
        if existing is not None:
            try:
                existing.remove()
            except Exception:
                pass
        sprite = EffectSprite(kind)
        self._effects_by_name[full_name] = sprite
        self.mount(sprite)
        # Position once now and again after the next refresh so the sprite
        # lands in the right spot whether or not card regions are settled.
        self._position_effect(full_name)
        self.call_after_refresh(self._position_effect, full_name)

    def dismiss_all_effects(self) -> bool:
        """Remove every active sprite. Returns True if anything was cleared."""
        if not self._effects_by_name:
            return False
        for sprite in list(self._effects_by_name.values()):
            try:
                sprite.remove()
            except Exception:
                pass
        self._effects_by_name.clear()
        return True

    def _position_effect(self, full_name: str) -> None:
        """Snap one sprite to its target card's top-right corner.

        Removes the sprite if the target card has been filtered out or
        unmounted -- a sprite without a card has nowhere to anchor.
        """
        sprite = self._effects_by_name.get(full_name)
        if sprite is None:
            return
        card = self._cards_by_name.get(full_name)
        if card is None:
            self._effects_by_name.pop(full_name, None)
            try:
                sprite.remove()
            except Exception:
                pass
            return
        try:
            region = card.region
        except Exception:
            return
        if region.width <= 0 or region.height <= 0:
            return
        x = region.x + max(0, region.width - SPRITE_WIDTH)
        y = region.y
        try:
            sprite.styles.offset = (x, y)
        except Exception:
            pass

    def _reposition_all_effects(self) -> None:
        for full_name in list(self._effects_by_name.keys()):
            self._position_effect(full_name)

    def _refresh_line_text(self) -> str:
        """Format the second-line refresh status shown in the header.

        Names all three facts in plain language (time of last refresh,
        countdown to next, interval) so auto-refresh is always legible
        at a glance.  Local-time is used for the last-refresh stamp
        because clock-on-wall is what users are comparing against.

        Drift note: ``next_refresh_at`` is set when ``_trigger_refresh``
        starts a refresh, so during a long-running refresh the displayed
        countdown briefly reflects the cycle already in flight. We mask
        this by showing "refreshing..." while ``is_refreshing`` is true
        rather than a misleading countdown.
        """
        state = self._state
        now = datetime.now(UTC)
        last_label = (
            state.last_refresh_at.astimezone().strftime("%H:%M:%S")
            if state.last_refresh_at is not None
            else None
        )
        remaining = (
            int((state.next_refresh_at - now).total_seconds())
            if state.next_refresh_at is not None
            else None
        )
        return format_header_refresh_line(
            is_refreshing=state.is_refreshing,
            last_refresh_label=last_label,
            next_refresh_remaining_seconds=remaining,
            interval_seconds=self._refresh_seconds,
        )

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
        stale_set = self._state.stale_repos
        for full_name, health in desired.items():
            team_info = self._state.repo_teams.get(full_name)
            accent = team_accent(team_info.primary, known_teams) if team_info else "#808080"
            label = self._state.team_labels.get(team_info.primary, "") if team_info else ""
            is_stale = full_name in stale_set
            existing = self._cards_by_name.get(full_name)
            if existing is None:
                card = RepoCard(
                    health=health,
                    theme_spec=theme_spec,
                    team_accent=accent,
                    team_label=label,
                )
                card.stale = is_stale
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
                if card.stale != is_stale:
                    card.stale = is_stale
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

    def refresh_error_drawer(self, state: AppState) -> None:
        """Re-render the error drawer and auto-open if new errors appeared."""
        has_new = self._error_drawer.refresh_content(state)
        if has_new:
            self._error_drawer.open()

    def toggle_error_drawer(self) -> None:
        self._error_drawer.toggle()

    def clear_errors(self, state: AppState) -> None:
        state.clear_errors()
        self._error_drawer.refresh_content(state)
        self._error_drawer.close()

    def toggle_detail_drawer(self) -> None:
        health = selected_health(self._state)
        if health is None:
            return
        content = self._detail_drawer_content(health)
        self._drawer.toggle("detail", content)

    def toggle_org_drawer(self) -> None:
        self._top_drawer.toggle(
            self._org_drawer_left_sections(),
            self._org_drawer_middle_sections(),
            self._org_drawer_right_sections(),
        )

    def cycle_right_drawer(self) -> None:
        """Cycle: closed -> repo detail -> system -> network -> closed."""
        if (
            not self._drawer.is_open
            and not self._system_drawer.is_open
            and not self._network_drawer.is_open
        ):
            # Closed -> open repo detail
            health = selected_health(self._state)
            if health is not None:
                content = self._detail_drawer_content(health)
                self._drawer.open_with("detail", content)
            return
        if self._drawer.is_open:
            # Detail open -> close detail, open system
            self._drawer.close()
            self._system_drawer.refresh_content(self._state)
            self._system_drawer.open()
            return
        if self._system_drawer.is_open:
            # System open -> close system, open network
            self._system_drawer.close()
            self._network_drawer.refresh_content(self._state)
            self._network_drawer.open()
            return
        # Network open -> close
        self._network_drawer.close()

    def cycle_left_drawer(self) -> None:
        """Toggle the AWS drawer open/closed."""
        if self._aws_drawer.is_open:
            self._aws_drawer.close()
        else:
            self._aws_drawer.refresh_content(self._state.aws_state)
            self._aws_drawer.open()

    def cycle_top_drawer(self) -> None:
        """Alias for toggle_org_drawer (w key)."""
        self.toggle_org_drawer()

    def toggle_system_drawer(self) -> None:
        """Direct open/close for the system drawer (``s`` key)."""
        if self._system_drawer.is_open:
            self._system_drawer.close()
            return
        # Close competing right-side drawers.
        if self._drawer.is_open:
            self._drawer.close()
        if self._network_drawer.is_open:
            self._network_drawer.close()
        self._system_drawer.refresh_content(self._state)
        self._system_drawer.open()

    def refresh_system_drawer(self) -> None:
        """Update the system drawer content if it is open."""
        if self._system_drawer.is_open:
            self._system_drawer.refresh_content(self._state)

    def toggle_network_drawer(self) -> None:
        """Direct open/close for the network/DNS drawer (``n`` key)."""
        if self._network_drawer.is_open:
            self._network_drawer.close()
            return
        # Close competing right-side drawers.
        if self._drawer.is_open:
            self._drawer.close()
        if self._system_drawer.is_open:
            self._system_drawer.close()
        self._network_drawer.refresh_content(self._state)
        self._network_drawer.open()

    def refresh_network_drawer(self) -> None:
        """Update the network drawer content if it is open."""
        if self._network_drawer.is_open:
            self._network_drawer.refresh_content(self._state)

    def refresh_aws_drawer(self) -> None:
        """Update the AWS drawer content if it is open."""
        if self._aws_drawer.is_open:
            self._aws_drawer.refresh_content(self._state.aws_state)

    def on_org_drawer_header_toggle(self, _message: OrgDrawerHeader.Toggle) -> None:
        self.toggle_org_drawer()

    def on_top_drawer_section_clicked(self, message: TopDrawer.SectionClicked) -> None:
        """Open the per-widget help modal when a drawer section is clicked."""
        message.stop()
        self.app.push_screen(WidgetHelpScreen(message.section_id))

    def on_aws_drawer_sso_login_requested(self, message: AwsDrawer.SsoLoginRequested) -> None:
        """Launch SSO login for the clicked profile."""
        message.stop()
        from ..dashboard.awsprobe import launch_sso_login

        profile_name = message.profile_name
        # Check current state to see if active
        aws_state = self._state.aws_state
        if aws_state is not None:
            for p in aws_state.profiles:
                if p.name == profile_name and p.status == "active":
                    self.app.notify(f"profile {profile_name} is active", timeout=3)
                    return
        launched = launch_sso_login(profile_name)
        if launched:
            self.app.notify(f"launching SSO for {profile_name}...", timeout=4)
        else:
            self.app.notify(f"failed to launch SSO for {profile_name}", severity="error", timeout=4)

    def _detail_drawer_content(self, health: RepoHealth) -> Text:
        status = health.status
        t = Text()
        t.append(f"{status.full_name}\n\n", style="bold")
        t.append(f"main ci: {status.main_status}\n")
        if status.has_dev_branch and status.dev_status:
            t.append(f"dev ci:  {status.dev_status}\n")
        # issues + PRs counts are emitted as OSC-8 hyperlinks so clicking the
        # number opens the matching GitHub listing. Matches the card's click
        # routing in spirit -- drawer widgets don't support per-line click
        # handlers, so the terminal's native link handler carries the click.
        issues_url = f"https://github.com/{status.full_name}/issues"
        pulls_url = f"https://github.com/{status.full_name}/pulls"
        t.append("issues:  ")
        t.append(str(status.open_issues), style=f"link {issues_url}")
        t.append("\n")
        t.append("prs:     ")
        t.append(
            f"{status.open_prs} ({status.draft_prs} drafts)",
            style=f"link {pulls_url}",
        )
        t.append("\n\n")
        self._append_deployments_block(t, health)
        if health.findings:
            t.append("findings:\n", style="bold")
            for finding in health.findings:
                t.append(f"  • {finding.check_name}: {finding.summary}\n")
        else:
            # Mirror the card's OK-state summary so the drawer is useful even
            # when the repo is fully green. Empty body -> just the closing hint.
            summary = self._healthy_summary_line(status, issues_url, pulls_url)
            if summary.plain:
                t.append_text(summary)
                t.append("\n")
        t.append("\npress d to close, enter for full drilldown.", style="dim")
        return t

    def _append_deployments_block(self, t: Text, health: RepoHealth) -> None:
        """Append a ``deployments:`` section listing each configured / auto link.

        Emits one line per link with the label, the URL host as the visible
        text, and an OSC-8 hyperlink so the user can click straight to the
        deployment. Auto-synthesised PyPI entries are rendered dim to signal
        they came from detection, not the user's yaml.
        """
        from . import deployments as dep

        links = dep.resolve_links(health.status)
        if not links:
            return
        t.append("deployments:\n", style="bold")
        for link in dep.sort_links_for_display(links):
            t.append(f"  {link.label}: ")
            # Strip the scheme for display; keep the full URL in the OSC-8 target.
            display = link.url.split("://", 1)[-1].rstrip("/")
            style = f"link {link.url}"
            if link.source == "auto":
                style = f"dim link {link.url}"
            t.append(display, style=style)
            t.append("\n")
        t.append("\n")

    def _healthy_summary_line(
        self,
        status: RepoStatus,
        issues_url: str,
        pulls_url: str,
    ) -> Text:
        """Single-line OK-state summary with clickable counts.

        Each count is wrapped in an OSC-8 hyperlink via ``style="link ..."`` so
        the terminal treats the number as directly clickable. Drafts share the
        PRs URL since GitHub's /pulls page lists both.
        """
        line = Text()
        pieces: list[tuple[str, str]] = []
        if status.open_issues > 0:
            noun = "issue" if status.open_issues == 1 else "issues"
            pieces.append((f"{status.open_issues} {noun}", issues_url))
        ready_prs = max(0, status.open_prs - status.draft_prs)
        if ready_prs > 0:
            noun = "pr" if ready_prs == 1 else "prs"
            pieces.append((f"{ready_prs} {noun}", pulls_url))
        if status.draft_prs > 0:
            noun = "draft" if status.draft_prs == 1 else "drafts"
            pieces.append((f"{status.draft_prs} {noun}", pulls_url))
        if not pieces:
            return line
        line.append("no findings. open: ", style="dim")
        for idx, (label, url) in enumerate(pieces):
            if idx:
                line.append(", ", style="dim")
            line.append(label, style=f"dim link {url}")
        return line

    # ------------------------------------------------------------------
    # Org drawer content
    # ------------------------------------------------------------------
    #
    # Each column of the org drawer is composed from a list of named
    # "sections". Splitting the columns this way lets the TopDrawer render
    # each section as its own Static widget so clicks can be attributed to
    # a specific widget (and open an explanatory modal). The section ids
    # used below must stay in sync with the keys in
    # ``augint_tools.dashboard.screens.widget_help.WIDGET_HELP``.

    def _org_drawer_left_sections(self) -> list[tuple[str, Text]]:
        """Sections that make up the left column of the org drawer."""
        state = self._state
        spec = get_theme(state.theme_name)
        sections: list[tuple[str, Text]] = []

        system = Text()
        self._append_system_block(system, spec)
        if system.plain:
            sections.append(("system", system))

        if not state.healths:
            empty = Text()
            empty.append("no data yet. press r to refresh.\n", style="dim")
            sections.append(("empty", empty))
            return sections

        ci = Text()
        self._append_ci_matrix(ci, state.healths, spec)
        sections.append(("ci_matrix", ci))
        return sections

    def _org_drawer_middle_sections(self) -> list[tuple[str, Text]]:
        """Sections that make up the middle column of the org drawer."""
        from .health import Severity as _Sev

        state = self._state
        spec = get_theme(state.theme_name)
        title = " + ".join(self._owners) if self._owners else self._org_name or "Organization"
        sections: list[tuple[str, Text]] = []

        if not state.healths:
            header = Text()
            header.append(f"{title} -- org dashboard\n\n", style="bold")
            header.append("no data yet. press r to refresh.\n\n", style="dim")
            sections.append(("empty", header))
            return sections

        by_sev: dict[_Sev, int] = {}
        for h in state.healths:
            by_sev[h.worst_severity] = by_sev.get(h.worst_severity, 0) + 1
        total = len(state.healths)
        ok_count = by_sev.get(_Sev.OK, 0)
        score = int((ok_count / total) * 100) if total else 0

        header = Text()
        header.append(f"{title}\n", style="bold")
        header.append(f"{total} repos  ·  {score}% green\n\n")
        sections.append(("header", header))

        sev_bar = Text()
        self._append_severity_bar(sev_bar, by_sev, total, spec, healths=state.healths)
        sections.append(("severity_bar", sev_bar))

        glyphs = Text()
        glyphs.append("repos    ", style="bold")
        self._append_repo_glyphs(glyphs, spec)
        glyphs.append("\n\n")
        sections.append(("repo_glyphs", glyphs))

        weather = Text()
        self._append_weather(weather, by_sev, state.healths, spec)
        sections.append(("weather", weather))

        pr_ages = Text()
        self._append_pr_ages(pr_ages, state.healths, spec)
        sections.append(("pr_ages", pr_ages))

        team_mix = Text()
        self._append_team_mix(team_mix, state, spec)
        if team_mix.plain:
            sections.append(("team_mix", team_mix))

        hint = Text()
        hint.append("press i or click header to close.", style="dim")
        sections.append(("hint", hint))
        return sections

    def _org_drawer_right_sections(self) -> list[tuple[str, Text]]:
        """Sections that make up the right column of the org drawer."""
        state = self._state
        spec = get_theme(state.theme_name)
        sections: list[tuple[str, Text]] = []
        if not state.healths:
            empty = Text()
            empty.append("--\n", style="dim")
            sections.append(("empty", empty))
            return sections

        check = Text()
        self._append_check_breakdown(check, state.healths, spec)
        sections.append(("check_breakdown", check))

        svc = Text()
        self._append_service_lib(svc, state.healths, spec)
        sections.append(("service_lib", svc))

        score = Text()
        self._append_score_histogram(score, state.healths, spec)
        sections.append(("score_histogram", score))

        errors = Text()
        self._append_recent_errors(errors, state, spec)
        sections.append(("recent_errors", errors))

        leader = Text()
        self._append_leaderboard(leader, state.healths, spec)
        if leader.plain:
            sections.append(("leaderboard", leader))
        return sections

    # Backwards-compatible accessors -- concatenate sections into a single
    # Text so older call sites (and tests) keep working unchanged.

    def _org_drawer_content(self) -> Text:
        """Left column as a single Text (concatenation of all sections)."""
        return _concat_sections(self._org_drawer_left_sections())

    def _org_drawer_middle_content(self) -> Text:
        """Middle column as a single Text (concatenation of all sections)."""
        return _concat_sections(self._org_drawer_middle_sections())

    def _org_drawer_right_content(self) -> Text:
        """Right column as a single Text (concatenation of all sections)."""
        return _concat_sections(self._org_drawer_right_sections())

    # ------------------------------------------------------------------
    # Right-column widget helpers
    # ------------------------------------------------------------------

    def _append_ci_matrix(self, t: Text, healths: list, spec) -> None:
        """Per-repo CI status as coloured dots (dev + main if service).

        Repo names are rendered in their team's accent colour and turned into
        terminal hyperlinks (OSC 8) pointing at the repo's GitHub Actions page.
        """
        t.append("ci matrix\n", style="bold")
        shown = healths[:24]
        known_teams = list(self._state.team_labels)
        for h in shown:
            status = h.status
            name = status.name[:16]
            team_info = self._state.repo_teams.get(status.full_name)
            accent = team_accent(team_info.primary, known_teams) if team_info else "#808080"
            link_url = f"https://github.com/{status.full_name}/actions"
            t.append("  ")
            t.append(f"{name:<16}", style=f"{accent} link {link_url}")
            t.append(" ")
            if status.has_dev_branch and status.dev_status:
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
        bar_width = 10
        for name, count in ordered[:6]:
            label = name.replace("_", " ")[:14]
            colour = spec.severity_colors.get(
                worst_sev.get(name, _Sev.OK), spec.severity_colors[_Sev.OK]
            )
            blocks = max(1, int(round(count / total * bar_width)))
            t.append(f"  {label:<14} ")
            t.append("\u2588" * blocks, style=colour)
            t.append(f" {count}\n", style="dim")
        t.append("\n")

    def _append_service_lib(self, t: Text, healths: list, spec) -> None:
        """Split of services vs libraries and their health."""
        from .health import Severity as _Sev

        services = [h for h in healths if h.status.has_dev_branch or h.status.looks_like_service]
        libs = [h for h in healths if not (h.status.has_dev_branch or h.status.looks_like_service)]
        t.append("service / lib\n", style="bold")

        def _line(label: str, bucket: list) -> None:
            if not bucket:
                t.append(f"  {label:<5} 0\n", style="dim")
                return
            ok = sum(1 for h in bucket if h.worst_severity == _Sev.OK)
            bad = len(bucket) - ok
            t.append(f"  {label:<5} {len(bucket):>3} ")
            if ok:
                t.append(f"{ok}ok", style=spec.severity_colors[_Sev.OK])
            if bad:
                if ok:
                    t.append("/")
                t.append(f"{bad}err", style=spec.severity_colors[_Sev.HIGH])
            t.append("\n")

        _line("services", services)
        _line("libs", libs)
        t.append("\n")

    @staticmethod
    def _display_score(raw_score: int) -> int:
        """Normalize internal score (0-10000) to display range (0-100)."""
        return min(100, max(0, raw_score // 100))

    def _append_score_histogram(self, t: Text, healths: list, spec) -> None:
        """Health-score histogram in 10-point buckets."""
        from .health import Severity as _Sev

        if not healths:
            return
        buckets = [0] * 10  # 0-9, 10-19, ..., 90-100
        for h in healths:
            ds = self._display_score(h.score)
            idx = min(9, max(0, ds // 10))
            buckets[idx] += 1
        peak = max(buckets) or 1
        glyphs = " \u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
        t.append("scores\n", style="bold")
        t.append("  0 ", style="dim")
        for bi, b in enumerate(buckets):
            gi = int(round((b / peak) * (len(glyphs) - 1)))
            colour = (
                spec.severity_colors[_Sev.CRITICAL]
                if b and bi < 5
                else spec.severity_colors[_Sev.OK]
            )
            t.append(glyphs[gi], style=colour)
        t.append(" 100\n", style="dim")
        avg = sum(self._display_score(h.score) for h in healths) / len(healths)
        t.append(f"  avg {avg:.0f}\n\n", style="dim")

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
            t.append("none", style="dim")
            t.append("\n\n")
            return
        bar_width = 18

        def bar(n: int, total: int, colour: str) -> None:
            if total <= 0:
                return
            blocks = max(1, int(round(n / total * bar_width))) if n > 0 else 0
            if blocks > 0:
                t.append("\u2588" * blocks, style=colour)

        bar(active, total_prs, spec.severity_colors[_Sev.OK])
        bar(stale, total_prs, spec.severity_colors[_Sev.HIGH])
        bar(drafts, total_prs, "dim")
        t.append(f" {active}", style="dim")
        if stale:
            t.append(f"/{stale}s", style=spec.severity_colors[_Sev.HIGH])
        if drafts:
            t.append(f"/{drafts}d", style="dim")
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
        bar_width = 18
        used = 0
        for i, (key, count) in enumerate(ordered):
            slot = int(count / total * bar_width) if total else 0
            if i == len(ordered) - 1:
                slot = bar_width - used
            used += slot
            if slot > 0:
                t.append("\u2588" * slot, style=_team_accent(key, known_teams))
        t.append("\n  ")
        legend_bits: list[str] = []
        for key, count in ordered[:4]:
            label = state.team_labels.get(key, key)
            legend_bits.append(f"{label} {count}")
        if len(ordered) > 4:
            legend_bits.append(f"+{len(ordered) - 4}")
        t.append(" · ".join(legend_bits), style="dim")
        t.append("\n\n")

    def _append_leaderboard(self, t: Text, healths: list, spec) -> None:
        """Worst-5 repos by score (lower = worse). Skip when everything green."""
        from .health import Severity as _Sev

        worst = [h for h in healths if h.worst_severity != _Sev.OK]
        if not worst:
            return
        worst.sort(key=lambda h: (h.score, int(h.worst_severity), h.status.name.lower()))
        t.append("worst 5\n", style="bold")
        for rank, h in enumerate(worst[:5], start=1):
            colour = spec.severity_colors.get(h.worst_severity, spec.severity_colors[_Sev.OK])
            first = next((f for f in h.findings if f.severity != _Sev.OK), None)
            summary = first.summary if first else h.worst_severity.name.lower()
            ds = self._display_score(h.score)
            t.append(f"  {rank}. ")
            t.append(f"{h.status.name[:16]:<16}", style="bold")
            t.append(f" {ds:>3}", style=colour)
            t.append(f" {summary[:20]}\n", style="dim")
        t.append("\n")

    def _append_severity_bar(
        self,
        t: Text,
        by_sev: dict,
        total: int,
        spec,  # ThemeSpec
        *,
        width: int = 24,
        healths: list | None = None,
    ) -> None:
        """Render a colour-segmented block bar representing severity distribution.

        When every repo is green we still surface useful OK-severity info by
        aggregating open-issue and open-PR counts across the whole workspace
        and rendering them as clickable hyperlinks to the user's GitHub
        dashboard (which scopes to repos they can see).
        """
        from .health import Severity as _Sev

        order = [
            (_Sev.CRITICAL, "crit"),
            (_Sev.HIGH, "high"),
            (_Sev.MEDIUM, "med"),
            (_Sev.LOW, "low"),
            (_Sev.OK, "ok"),
        ]
        t.append("health [", style="bold")
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
        non_ok_pieces: list[tuple[str, str | None]] = []
        for sev, label in order:
            if sev is _Sev.OK:
                continue
            count = by_sev.get(sev, 0)
            if count:
                non_ok_pieces.append((f"{label} {count}", None))
        pieces: list[tuple[str, str | None]] = list(non_ok_pieces)
        # When nothing is wrong, surface aggregated OK-state info (open issues
        # / PRs) as clickable totals instead of a flat "all green" string. The
        # legend keeps a trailing "ok N" chip for continuity with the prior
        # behaviour.
        ok_count = by_sev.get(_Sev.OK, 0)
        if not non_ok_pieces and healths:
            pieces.extend(self._healthy_org_pieces(healths))
        if ok_count:
            pieces.append((f"ok {ok_count}", None))
        if pieces:
            for idx, (label, url) in enumerate(pieces):
                if idx:
                    t.append("  ")
                if url:
                    t.append(label, style=f"link {url}")
                else:
                    t.append(label)
        else:
            t.append("all green")
        t.append("\n\n")

    def _healthy_org_pieces(self, healths: list) -> list[tuple[str, str | None]]:
        """Aggregate OK-state totals across all healths into clickable labels.

        Returns ``(label, url)`` pairs; ``url`` is attached as an OSC-8 link on
        the rendered text. Targets GitHub's global issues/pulls dashboard --
        when signed in this scopes to repos the user can see, giving a single
        click-through that works across all the owners shown in the header.
        """
        total_issues = sum(max(0, h.status.open_issues) for h in healths)
        total_prs = sum(max(0, h.status.open_prs) for h in healths)
        pieces: list[tuple[str, str | None]] = []
        if total_issues > 0:
            pieces.append(
                (
                    f"issues {total_issues}",
                    "https://github.com/issues?q=is%3Aopen+is%3Aissue",
                )
            )
        if total_prs > 0:
            pieces.append(
                (
                    f"prs {total_prs}",
                    "https://github.com/pulls?q=is%3Aopen+is%3Apr",
                )
            )
        return pieces

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
        bar_width = 14

        if ram is not None:
            used_frac = ram.used_fraction
            color = spec.severity_colors[_Sev.OK]
            if used_frac >= 0.90:
                color = spec.severity_colors[_Sev.CRITICAL]
            elif used_frac >= 0.75:
                color = spec.severity_colors[_Sev.HIGH]
            elif used_frac >= 0.60:
                color = spec.severity_colors[_Sev.MEDIUM]
            pct = int(round(used_frac * 100))
            t.append("  RAM  ")
            t.append(_progress_bar(used_frac, bar_width), style=color)
            t.append(f" {pct}% {ram.used_gb:.0f}/{ram.total_gb:.0f}G\n")

        if gpu is not None:
            t.append(f"  GPU  {gpu.name[:16]}")
            extras: list[str] = []
            if gpu.temp_c is not None:
                extras.append(f"{gpu.temp_c}C")
            if gpu.power_w is not None:
                extras.append(f"{gpu.power_w:.0f}W")
            if extras:
                t.append(" " + " ".join(extras), style="dim")
            t.append("\n")

            util_color = spec.severity_colors[_Sev.OK]
            if gpu.util_pct >= 90:
                util_color = spec.severity_colors[_Sev.HIGH]
            elif gpu.util_pct >= 60:
                util_color = spec.severity_colors[_Sev.MEDIUM]
            t.append("  util ")
            t.append(_progress_bar(gpu.util_fraction, bar_width), style=util_color)
            t.append(f" {gpu.util_pct}%\n")

            vram_frac = gpu.vram_fraction
            vram_color = spec.severity_colors[_Sev.OK]
            if vram_frac >= 0.90:
                vram_color = spec.severity_colors[_Sev.CRITICAL]
            elif vram_frac >= 0.75:
                vram_color = spec.severity_colors[_Sev.HIGH]
            elif vram_frac >= 0.60:
                vram_color = spec.severity_colors[_Sev.MEDIUM]
            vram_pct = int(round(vram_frac * 100))
            t.append("  vram ")
            t.append(_progress_bar(vram_frac, bar_width), style=vram_color)
            t.append(f" {vram_pct}% {gpu.vram_used_gb:.0f}/{gpu.vram_total_gb:.0f}G\n")
        t.append("\n")


class DashboardApp(App[None]):
    """V2 interactive health dashboard for GitHub repositories."""

    TITLE = "ai-tools dashboard"
    ANIMATION_LEVEL = "full"
    CSS = """
    Screen {
        layers: base overlay effects;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("ctrl+c", "quit", show=False),
        Binding("r", "refresh_now", "Refresh"),
        Binding("1", "open_filter_panel", "Filter"),
        Binding("2", "cycle_sort", "Sort"),
        Binding("3", "cycle_layout", "Layout"),
        Binding("4", "cycle_theme", "Theme"),
        Binding("5", "toggle_flash", "Blink"),
        Binding("e", "toggle_errors", "Errors"),
        Binding("a", "cycle_left_drawer", "AWS"),
        Binding("w", "cycle_top_drawer", "Org"),
        Binding("s", "toggle_system_drawer", "System"),
        Binding("n", "toggle_network_drawer", "DNS"),
        Binding("d", "cycle_right_drawer", "Drawer"),
        # Displaced from lowercase `w` when the org drawer claimed it; keep
        # a hidden alias so the workspace-hide toggle doesn't disappear.
        Binding("W", "toggle_hide_workspace", "Workspace", show=False),
        Binding("i", "toggle_org", "Org", show=False),
        Binding("E", "clear_errors", "Clear Errors", show=False),
        Binding("m", "manage_repos", "Repos"),
        Binding("O", "manage_orgs", "Orgs"),
        Binding("z", "open_deployment_main", "Prod", show=False),
        Binding("x", "open_deployment_dev", "Dev", show=False),
        Binding("c", "open_deployment_3", show=False),
        Binding("v", "open_deployment_4", show=False),
        Binding("b", "open_deployment_5", show=False),
        Binding("f", "manage_deployments", "URLs"),
        Binding("question_mark", "show_help", "Help"),
        Binding("f5", "full_restart", "Restart", show=False),
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
        refresh_seconds: int = 60,
        initial_theme: str = "default",
        initial_layout: str = "packed",
        health_config: dict | None = None,
        org_name: str = "",
        owners: list[str] | None = None,
        skip_refresh: bool = False,
        github_client: Github | None = None,
        auto_discover: bool = False,
        saved_prefs: DashboardPrefs | None = None,
    ) -> None:
        super().__init__()
        self._repos = list(repos or [])
        self._refresh_seconds = refresh_seconds
        self._health_config = health_config or {}
        self._org_name = org_name
        self._owners: list[str] = list(owners) if owners else ([org_name] if org_name else [])
        self._skip_refresh = skip_refresh
        self._github_client = github_client
        # Inject the Github client into the YAML compliance engine's config
        # slot so its check can fetch ``standards.yaml`` via the same auth
        # session the GraphQL dashboard uses. Keeps the engine off the
        # network path when no client is provided (tests, offline mode).
        engine_cfg = self._health_config.setdefault("standards_engine", {})
        engine_cfg.setdefault("gh", self._github_client)
        self._auto_discover = auto_discover
        # Original repo names -- used to scope re-listing in non-discover mode.
        self._original_repo_names: set[str] = {r.full_name for r in self._repos}

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
        self._restart_requested: bool = False

        # Disabled repos -- excluded from refresh and all views.
        self._disabled_repos: set[str] = set()

        # Disabled orgs -- excluded from auto-discovery (like disabled_repos).
        self._disabled_orgs: set[str] = set()

        # Team assignments change rarely, so we fetch them on a much slower
        # cadence than the main refresh. Keeps the GraphQL point-cost bounded
        # even when the user drops --refresh-seconds into the tens of seconds.
        self._teams_ttl_seconds: int = 300
        self._last_teams_refresh_at: datetime | None = None

        # Cache for failing-run detail keyed by (full_name, branch, head_sha).
        # A failing branch keeps the same head commit until someone pushes a
        # fix or a retry -- so after the first detail fetch we never hit REST
        # again for the same failure.
        self._failing_detail_cache: dict[
            tuple[str, str, str],
            tuple[str | None, str | None],
        ] = {}

        # Apply remaining saved preferences (sort, filters, panel width, flash).
        # Theme and layout are already applied via initial_theme/initial_layout
        # which the caller resolves from saved prefs + CLI overrides.
        if saved_prefs is not None:
            self.state.sort_mode = saved_prefs.sort_mode
            self.state.active_filters = set(saved_prefs.active_filters)
            self.state.hide_workspace = saved_prefs.hide_workspace
            self.state.panel_width = saved_prefs.panel_width
            self._flash_enabled = saved_prefs.flash_enabled
            self._disabled_repos = set(saved_prefs.disabled_repos)
            self._disabled_orgs = set(saved_prefs.disabled_orgs)

    # ---- preferences ----

    def _save_prefs(self) -> None:
        """Persist the current UI preferences to disk."""
        save_prefs(
            DashboardPrefs(
                theme_name=self.state.theme_name,
                layout_name=self.state.layout_name,
                sort_mode=self.state.sort_mode,
                active_filters=sorted(self.state.active_filters),
                panel_width=self.state.panel_width,
                flash_enabled=self._flash_enabled,
                hide_workspace=self.state.hide_workspace,
                disabled_repos=sorted(self._disabled_repos),
                disabled_orgs=sorted(self._disabled_orgs),
            )
        )

    # ---- lifecycle ----

    def on_mount(self) -> None:
        self._load_theme_css(self.state.theme_name)
        # Exclude disabled repos from the initial repo list so they don't
        # appear in the first paint and don't get refreshed.
        if self._disabled_repos:
            self._repos = [r for r in self._repos if r.full_name not in self._disabled_repos]
        # Cache-first: load before mounting so the first paint shows data.
        restrict = {r.full_name for r in self._repos} if self._repos else None
        bootstrap_from_cache(self.state, restrict_to=restrict)
        apply_open_source_team(self.state)

        # Seed the AWS drawer from the on-disk cache so opening it doesn't
        # stare at "loading..." while subprocesses fan out.
        from .awsprobe import load_aws_cache

        cached_aws = load_aws_cache()
        if cached_aws is not None:
            self.state.aws_state = cached_aws

        self._main = MainScreen(
            self.state,
            self._org_name,
            owners=self._owners,
            refresh_seconds=(0 if self._skip_refresh else self._refresh_seconds),
        )
        self.push_screen(self._main)

        if self._repos and not self._skip_refresh:
            # Seed the countdown before the first worker lands so the
            # status bar shows "next refresh in ..." from paint zero.
            # _trigger_refresh also sets next_refresh_at, but seeding
            # here keeps the behaviour visible even when _trigger_refresh
            # is mocked out in tests.
            self.state.next_refresh_at = datetime.now(UTC) + timedelta(
                seconds=self._refresh_seconds
            )
            # Trigger the first refresh immediately.  _trigger_refresh
            # flips ``is_refreshing`` to True synchronously so the first
            # StatusBar paint (driven by the tick 1s later) shows
            # "loading data..." / "refreshing..." rather than going blank
            # for the 20-30s the initial GitHub fetch takes.
            self.set_interval(self._refresh_seconds, self._trigger_refresh)
            self._trigger_refresh()
        self.set_interval(1.0, self._tick_status)
        self.set_interval(_FLASH_TICK_SECONDS, self._tick_flash)
        # Probe host RAM / GPU in the background: once at startup so the
        # first org-drawer open already shows numbers, then every 3s while
        # the drawer is visible (see ``_tick_sysmeter``). Suppressed when
        # ``skip_refresh`` is set so Pilot tests don't spawn the worker.
        if not self._skip_refresh:
            self._refresh_sysmeter()
        self.set_interval(3.0, self._tick_sysmeter)

        # System probe (CPU, docker, network): refresh every 5s while the
        # system drawer is open. No initial probe at mount -- it fires on
        # first drawer open and then periodically while open.
        self.set_interval(5.0, self._tick_system_probe)

        # Lightweight ping to 1.1.1.1 every 3s -- always runs so the
        # status bar connectivity chip stays live.
        if not self._skip_refresh:
            self._refresh_ping()
        self.set_interval(3.0, self._tick_ping)

        # DNS resolution check for deployment URLs every 30s. Runs
        # unconditionally so the status bar can show DNS failure counts
        # even when the network drawer is closed.
        if not self._skip_refresh:
            self._refresh_dns()
        self.set_interval(30.0, self._tick_dns)

        # AWS probe: refresh every 30s while the AWS drawer is open.
        # No initial probe at mount -- fires on first drawer open.
        self.set_interval(30.0, self._tick_aws_probe)

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

    async def action_full_restart(self) -> None:
        """Re-exec the process to pick up code changes (development helper)."""
        self._save_prefs()
        self._restart_requested = True
        self.state.cancel_requested = True
        try:
            self.workers.cancel_all()
        except Exception:
            pass
        self.exit()

    # ---- refresh workers ----

    def _trigger_refresh(self) -> None:
        if self.state.is_refreshing:
            return  # Previous refresh still running.
        self.state.is_refreshing = True
        self.state.next_refresh_at = datetime.now(UTC) + timedelta(seconds=self._refresh_seconds)
        self.run_worker(self._do_refresh_sync, thread=True, exit_on_error=False)

    def _do_refresh_sync(self) -> None:
        logger.debug("refresh: starting")
        try:
            self._do_refresh_inner()
            self.state.consecutive_errors = 0
            self.state.last_error_message = None
            logger.debug("refresh: completed successfully")
        except Exception as exc:
            self.state.is_refreshing = False
            self.state.consecutive_errors += 1
            msg = f"{exc.__class__.__name__}: {exc}"
            self.state.last_error_message = msg
            self.state.log_error("refresh", msg)
            logger.error(f"refresh: top-level failure: {msg}")
            self.call_from_thread(self._rerender)

    def _refresh_repo_list(self) -> None:
        """Re-list repos from all owners to pick up additions and removals.

        Called at the start of each refresh cycle.  When ``auto_discover``
        is True the full current listing across all owners replaces the
        internal repo list so new repos appear automatically.  In other
        modes only repos from the original selection that still exist are
        kept -- deleted repos are dropped but new repos are not added.
        """
        if self._github_client is None or not self._owners:
            logger.debug("refresh: skipping repo list (no client or owners)")
            return
        try:
            fresh = strip_dotfile_repos(list_repos_multi(self._github_client, self._owners))
            logger.debug(f"refresh: listing returned {len(fresh)} repos across {self._owners}")
        except Exception as exc:
            self.state.log_error("refresh", f"repo list refresh: {exc.__class__.__name__}: {exc}")
            logger.warning(f"refresh: repo list failed: {exc}")
            return  # Keep current list on failure.

        prev_names = {r.full_name for r in self._repos}
        fresh_names = {r.full_name for r in fresh}

        if self._auto_discover:
            self._repos = [r for r in fresh if r.full_name not in self._disabled_repos]
        else:
            # Keep only repos the user originally selected that still exist.
            self._repos = [
                r
                for r in fresh
                if r.full_name in self._original_repo_names
                and r.full_name not in self._disabled_repos
            ]
            # Shrink the original set so we stop warning on every cycle.
            gone = self._original_repo_names - fresh_names
            self._original_repo_names -= gone

        # Detect repos that disappeared (archived, deleted, or org removed).
        current_names = {r.full_name for r in self._repos}
        vanished = prev_names - current_names
        for name in sorted(vanished):
            self.state.log_error("refresh", f"{name}: removed (deleted or archived)")
            logger.info(f"refresh: {name} removed (deleted or archived)")

        # Clean up state for repos no longer in the list.
        removed = set(self.state.health_by_name.keys()) - current_names
        if removed:
            self.state.healths = [
                h for h in self.state.healths if h.status.full_name not in removed
            ]
            for name in removed:
                self.state.health_by_name.pop(name, None)
                self.state.repo_teams.pop(name, None)

        # Note: stale refresh errors from previous cycles are cleared at
        # the top of _do_refresh_inner() so errors about archived/deleted
        # repos don't linger.  The "removed" log entries above will survive
        # because they're logged *after* that blanket clear.

    def _do_refresh_inner(self) -> None:
        # Clear stale refresh errors from the previous cycle.  If the same
        # problems still exist they'll be re-logged during this cycle.
        self.state.errors = [e for e in self.state.errors if e.source != "refresh"]

        # Phase 0: reconcile the repo list against the live org listing.
        self._refresh_repo_list()
        if not self._repos:
            return

        ordered_names: list[str] = [r.full_name for r in self._repos]
        failed_repos: set[str] = set()

        # Phase 1: batched GraphQL fetch -- one query per 25 repos covers
        # status, PRs, issues, root tree, Renovate config, and pipeline.yaml.
        if self._github_client is None:
            logger.error("refresh: no github client available, skipping")
            return

        logger.debug(f"refresh: graphql workspace fetch for {len(self._repos)} repos")
        try:
            snapshot_set = fetch_workspace_snapshot(self._github_client, self._repos)
        except Exception as exc:
            self.state.log_error("refresh", f"graphql: {exc.__class__.__name__}: {exc}")
            logger.error(f"refresh: graphql failed: {exc.__class__.__name__}: {exc}")
            return

        logger.debug(
            f"refresh: graphql cost={snapshot_set.rate_limit_cost} "
            f"remaining={snapshot_set.rate_limit_remaining}"
        )
        for full_name, err in snapshot_set.errored.items():
            failed_repos.add(full_name)
            self.state.log_error("refresh", f"{full_name}: {err}")
            logger.warning(f"refresh: {full_name}: {err}")

        if self.state.cancel_requested:
            return

        # Phase 2: build RepoStatus from snapshots.
        status_by_name: dict[str, RepoStatus] = {}
        snapshot_by_name = snapshot_set.by_full_name
        failing_targets: list[tuple[Repository, str]] = []  # (repo, branch)

        for repo in self._repos:
            snapshot = snapshot_by_name.get(repo.full_name)
            if snapshot is None:
                # Keep previous state if the repo errored this cycle.
                prev = self.state.health_by_name.get(repo.full_name)
                if prev is not None:
                    status_by_name[repo.full_name] = prev.status
                else:
                    status_by_name[repo.full_name] = RepoStatus(
                        name=getattr(repo, "name", "?"),
                        full_name=repo.full_name,
                        has_dev_branch=False,
                        main_status="unknown",
                        main_error=None,
                        dev_status=None,
                        dev_error=None,
                        open_issues=0,
                        open_prs=0,
                        draft_prs=0,
                    )
                continue

            status = build_status_from_snapshot(snapshot)
            status_by_name[repo.full_name] = status
            if status.main_status == "failure":
                failing_targets.append((repo, status.default_branch))
            if status.has_dev_branch and status.dev_status == "failure":
                failing_targets.append((repo, "dev"))

        # Phase 2b: populate failing-run detail. GraphQL's statusCheckRollup
        # doesn't expose the failing job/step name, so we fall back to REST
        # -- but cache keyed by (full_name, branch, head_sha) so each failing
        # commit costs exactly one REST call regardless of how many refresh
        # cycles it stays failing.
        if failing_targets and not self.state.cancel_requested:
            to_fetch: list[tuple[Repository, str]] = []
            for repo, branch in failing_targets:
                snapshot = snapshot_by_name.get(repo.full_name)
                if snapshot is None:
                    continue
                head_sha = (
                    snapshot.dev_head_sha if branch == "dev" else snapshot.main_head_sha
                ) or ""
                cache_key = (repo.full_name, branch, head_sha)
                cached = self._failing_detail_cache.get(cache_key)
                if cached is not None:
                    self._apply_failing_detail(status_by_name, repo.full_name, branch, *cached)
                else:
                    to_fetch.append((repo, branch))

            if to_fetch:
                logger.debug(
                    f"refresh: failing-run detail cache miss for {len(to_fetch)} branch(es)"
                )
                detail_workers = min(4, len(to_fetch))
                with ThreadPoolExecutor(max_workers=detail_workers) as pool:
                    detail_futures = {
                        pool.submit(fetch_failing_run_detail, repo, branch): (
                            repo.full_name,
                            branch,
                        )
                        for repo, branch in to_fetch
                    }
                    for detail_fut in as_completed(detail_futures):
                        if self.state.cancel_requested:
                            pool.shutdown(wait=False, cancel_futures=True)
                            return
                        detail_full_name, detail_branch = detail_futures[detail_fut]
                        try:
                            detail_err, detail_since = detail_fut.result()
                        except Exception:
                            continue
                        snapshot = snapshot_by_name.get(detail_full_name)
                        head_sha = (
                            (
                                snapshot.dev_head_sha
                                if detail_branch == "dev"
                                else snapshot.main_head_sha
                            )
                            if snapshot is not None
                            else ""
                        ) or ""
                        self._failing_detail_cache[(detail_full_name, detail_branch, head_sha)] = (
                            detail_err,
                            detail_since,
                        )
                        self._apply_failing_detail(
                            status_by_name,
                            detail_full_name,
                            detail_branch,
                            detail_err,
                            detail_since,
                        )

            # Trim cache entries for branches that are no longer failing so it
            # doesn't grow unbounded over a long session.
            live_keys = {
                (repo.full_name, branch, snapshot_by_name[repo.full_name].dev_head_sha or "")
                if branch == "dev"
                else (repo.full_name, branch, snapshot_by_name[repo.full_name].main_head_sha or "")
                for repo, branch in failing_targets
                if repo.full_name in snapshot_by_name
            }
            stale_keys = set(self._failing_detail_cache) - live_keys
            for key in stale_keys:
                self._failing_detail_cache.pop(key, None)

        # Phase 3: team assignments -- one batched GraphQL query per owner,
        # fetched on a slow cadence (teams_ttl_seconds) because team
        # membership changes rarely. Fresh snapshot replaces state.repo_teams
        # in the commit step.
        teams_snapshot = None
        if self._owners and not self.state.cancel_requested:
            now = datetime.now(UTC)
            is_stale = (
                self._last_teams_refresh_at is None
                or (now - self._last_teams_refresh_at).total_seconds() >= self._teams_ttl_seconds
            )
            if is_stale:
                logger.debug(f"refresh: graphql teams fetch for {len(self._owners)} owner(s)")
                try:
                    teams_snapshot = fetch_workspace_teams(self._github_client, self._owners)
                    self._last_teams_refresh_at = now
                    logger.debug(
                        f"refresh: teams graphql cost={teams_snapshot.rate_limit_cost} "
                        f"remaining={teams_snapshot.rate_limit_remaining}"
                    )
                    for owner, err in teams_snapshot.errored.items():
                        self.state.log_error("refresh", f"teams {owner}: {err}")
                        logger.warning(f"refresh: teams {owner}: {err}")
                except Exception as exc:
                    self.state.log_error(
                        "refresh", f"teams graphql: {exc.__class__.__name__}: {exc}"
                    )
                    logger.error(f"refresh: teams graphql failed: {exc}")

        # Preserve original repo ordering.
        statuses = [status_by_name[n] for n in ordered_names]

        # Phase 4: run health checks off-wire (every check reads pre-fetched
        # context data; none make their own REST call).
        healths: list[RepoHealth] = []
        for repo, status in zip(self._repos, statuses, strict=True):
            if self.state.cancel_requested:
                return
            snapshot = snapshot_by_name.get(status.full_name)
            try:
                if snapshot is not None:
                    rpath, rtext = pick_renovate_config(snapshot)
                    ppath, ptext = pick_pipeline_yaml(snapshot)
                    ctx = FetchContext(
                        pulls=list(snapshot.pull_requests),
                        issues=list(snapshot.issues),
                        renovate_config_path=rpath,
                        renovate_config_text=rtext,
                        pipeline_path=ppath,
                        pipeline_text=ptext,
                        pyproject_text=pick_pyproject(snapshot),
                        package_json_text=pick_package_json(snapshot),
                        precommit_text=pick_precommit(snapshot),
                        rulesets=list(snapshot.rulesets),
                        main_head_sha=snapshot.main_head_sha,
                        owner=snapshot.owner,
                        repo_name=snapshot.name,
                    )
                else:
                    ctx = FetchContext()
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
        self.call_from_thread(self._commit_refresh, healths, teams_snapshot, failed_repos)

    def _apply_failing_detail(
        self,
        status_by_name: dict[str, RepoStatus],
        full_name: str,
        branch: str,
        err: str | None,
        failing_since: str | None,
    ) -> None:
        """Write failing-run detail onto the status for the given branch."""
        status = status_by_name.get(full_name)
        if status is None:
            return
        if branch == status.default_branch:
            status.main_error = err
            status.main_failing_since = failing_since
        elif branch == "dev":
            status.dev_error = err
            status.dev_failing_since = failing_since

    def _commit_refresh(
        self,
        healths: list[RepoHealth],
        teams_snapshot: TeamsSnapshot | None = None,
        failed_repos: set[str] | None = None,
    ) -> None:
        # Merge the batched teams snapshot if this refresh rebuilt it. The
        # main thread is the only place that mutates repo_teams / team_labels.
        if teams_snapshot is not None:
            known_repos = [h.status.full_name for h in healths]
            merge_teams_snapshot(self.state, teams_snapshot, known_repos)
            logger.debug(
                f"commit: merged teams for {len(teams_snapshot.by_full_name)} repos, "
                f"{len(self.state.team_labels)} known teams"
            )
        apply_open_source_team(self.state)
        # Carry forward / stamp warning-transition timestamps *before* we swap
        # the healths list, so the card-flash logic can tell whether yellow is
        # new (ok -> warning) or established. Timestamps on the RepoStatus side
        # (main_failing_since / dev_failing_since) come straight from GitHub.
        self._update_warning_since(healths)
        # Detect ok<->critical transitions against the previous snapshot
        # before we overwrite it. Sprite spawning has to happen *after* the
        # rerender below so the card it anchors to has a fresh region.
        transitions = self._detect_severity_transitions(healths)
        self.state.healths = healths
        self.state.health_by_name = {h.status.full_name: h for h in healths}
        self.state.stale_repos = failed_repos or set()
        self.state.last_refresh_at = datetime.now(UTC)
        self.state.is_refreshing = False
        vis = visible_healths(self.state)
        logger.debug(
            f"commit: {len(healths)} total, {len(vis)} visible, "
            f"filters={self.state.active_filters or 'none'}, "
            f"sort={self.state.sort_mode}, errors={len(self.state.errors)}"
        )
        self._rerender()
        if transitions and self._main is not None:
            for full_name, kind in transitions:
                self._main.spawn_effect(full_name, kind)

    def _detect_severity_transitions(
        self, healths: list[RepoHealth]
    ) -> list[tuple[str, EffectKind]]:
        """Return (full_name, sprite_kind) for cards that just flipped class.

        Fireworks fire on any non-ok -> ok transition; mushroom clouds fire
        on any non-critical -> critical transition. Skips the first commit
        (no prior data) so we don't fireworks-spam every card on startup.
        """
        prior = self.state.health_by_name
        if not prior:
            return []
        out: list[tuple[str, EffectKind]] = []
        for h in healths:
            full_name = h.status.full_name
            prev = prior.get(full_name)
            if prev is None:
                continue
            prev_class = _card_severity_class(prev)
            new_class = _card_severity_class(h)
            if prev_class == new_class:
                continue
            if new_class == "ok":
                out.append((full_name, "fireworks"))
            elif new_class == "critical":
                out.append((full_name, "mushroom"))
        return out

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
        self.call_from_thread(self._rerender_org_drawer)

    # ---- system probe worker ----

    def _refresh_system_probe(self) -> None:
        """Kick a background system probe (CPU, docker, network)."""
        self.run_worker(self._refresh_system_probe_sync, thread=True, exit_on_error=False)

    def _refresh_system_probe_sync(self) -> None:
        try:
            snapshot = probe_system()
        except Exception as exc:
            self.state.log_error("sysprobe", f"{exc.__class__.__name__}: {exc}")
            return
        self.state.system_snapshot = snapshot
        self.call_from_thread(self._update_system_drawer)

    def _update_system_drawer(self) -> None:
        if self._main is not None:
            self._main.refresh_system_drawer()

    def _tick_system_probe(self) -> None:
        """Periodic system probe -- only while system drawer is open."""
        if self._main is None:
            return
        if not self._main._system_drawer.is_open:
            return
        self._refresh_system_probe()

    # ---- ping worker ----

    def _refresh_ping(self) -> None:
        """Kick a background ping probe (TCP to 1.1.1.1)."""
        self.run_worker(self._refresh_ping_sync, thread=True, exit_on_error=False)

    def _refresh_ping_sync(self) -> None:
        try:
            result = probe_ping()
        except Exception as exc:
            self.state.log_error("ping", f"{exc.__class__.__name__}: {exc}")
            return
        self.state.ping_result = result
        self.call_from_thread(self._update_ping_display)

    def _update_ping_display(self) -> None:
        """Push the new ping result to the status bar and network drawer."""
        if self._main is not None:
            self._main._status_bar.tick()
            self._main.refresh_network_drawer()

    def _tick_ping(self) -> None:
        """Periodic ping -- always runs for the status bar indicator."""
        if self._main is None:
            return
        self._refresh_ping()

    # ---- DNS probe worker ----

    def _refresh_dns(self) -> None:
        """Kick a background DNS resolution check for deployment URLs."""
        self.run_worker(self._refresh_dns_sync, thread=True, exit_on_error=False)

    def _refresh_dns_sync(self) -> None:
        try:
            from .deployments import load_deployments

            links_by_repo = load_deployments()
            urls_by_repo: dict[str, list[str]] = {}
            for repo, links in links_by_repo.items():
                urls_by_repo[repo] = [link.url for link in links]
            results = probe_dns(urls_by_repo)
        except Exception as exc:
            self.state.log_error("dns", f"{exc.__class__.__name__}: {exc}")
            return
        self.state.dns_results = results
        self.call_from_thread(self._update_dns_display)

    def _update_dns_display(self) -> None:
        """Push DNS results to the status bar and network drawer."""
        if self._main is not None:
            self._main._status_bar.tick()
            self._main.refresh_network_drawer()

    def _tick_dns(self) -> None:
        """Periodic DNS check -- always runs for the status bar indicator."""
        if self._main is None:
            return
        self._refresh_dns()

    # ---- AWS probe worker ----

    def _refresh_aws_probe(self) -> None:
        """Refresh AWS profile status from on-disk state.

        Runs synchronously on the UI thread: the local probe reads
        ``~/.aws/config`` and ``~/.aws/sso/cache`` only and finishes in
        well under one frame, so there's no reason to hand off to a
        worker. The previous worker-based path was firing per-profile
        ``aws sts`` subprocesses, which is what made opening the drawer
        feel like the UI had frozen.
        """
        from .awsprobe import probe_aws_local, save_aws_cache

        try:
            aws_state = probe_aws_local(previous_state=self.state.aws_state)
        except Exception as exc:
            self.state.log_error("awsprobe", f"{exc.__class__.__name__}: {exc}")
            self._update_aws_drawer()
            return
        self.state.aws_state = aws_state
        try:
            save_aws_cache(aws_state)
        except OSError as exc:
            self.state.log_error("awsprobe", f"cache save failed: {exc}")
        self._update_aws_drawer()

    def _update_aws_drawer(self) -> None:
        if self._main is not None:
            self._main.refresh_aws_drawer()

    def _tick_aws_probe(self) -> None:
        """Periodic AWS probe -- only while AWS drawer is open."""
        if self._main is None:
            return
        if not self._main._aws_drawer.is_open:
            return
        self._refresh_aws_probe()

    # ---- render glue ----

    def _rerender(self) -> None:
        if self._main is not None:
            try:
                self._main.rerender()
                self._main.refresh_error_drawer(self.state)
            except Exception as exc:
                self.state.log_error("ui", f"{exc.__class__.__name__}: {exc}")

    def _rerender_org_drawer(self) -> None:
        if self._main is not None:
            try:
                self._main.rerender_org_drawer()
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
        self._save_prefs()
        self.notify(f"sort: {self.state.sort_mode}", timeout=2)

    def action_open_filter_panel(self) -> None:
        def _on_dismiss(selected: set[str] | None) -> None:
            if selected is None:
                return
            # ``FilterChanged`` has already applied the selection live;
            # final state equals ``selected`` here, so we just persist
            # and announce it without doing another rerender pass.
            self.state.active_filters = selected
            self._save_prefs()
            count = len(visible_healths(self.state))
            n = len(selected)
            label = "all repos" if n == 0 else f"{n} filter{'s' if n != 1 else ''}"
            self.notify(f"filter: {label} -- {count} repos", timeout=2)

        self.push_screen(FilterPanel(self.state), callback=_on_dismiss)

    def on_filter_panel_filter_changed(self, message: FilterPanel.FilterChanged) -> None:
        """Apply filter-panel selections live, without waiting for dismiss.

        Without this handler the cards only re-filter when the panel
        closes -- users watching the list behind the panel see nothing
        happen and assume the app has hung.
        """
        self.state.active_filters = set(message.selected)
        self._rerender()

    def action_cycle_layout(self) -> None:
        layouts = list_layouts()
        try:
            idx = layouts.index(self.state.layout_name)
        except ValueError:
            idx = -1
        self.state.layout_name = layouts[(idx + 1) % len(layouts)]
        self._rerender()
        self._save_prefs()
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
        self._save_prefs()
        self.notify(f"theme: {new_theme}", timeout=2)

    def action_toggle_hide_workspace(self) -> None:
        """Hide or show workspace repos."""
        self.state.hide_workspace = not self.state.hide_workspace
        self._rerender()
        self._save_prefs()
        label = "hidden" if self.state.hide_workspace else "shown"
        self.notify(f"workspace repos: {label}", timeout=2)

    def action_toggle_flash(self) -> None:
        """Enable or disable the recently-broken border flash."""
        self._flash_enabled = not self._flash_enabled
        if not self._flash_enabled and self._main is not None:
            # Clear the flash phase class immediately so nothing stays lit.
            self._main.apply_flash_phase(False, window_seconds=FLASH_WINDOW_SECONDS)
        self._save_prefs()
        state = "on" if self._flash_enabled else "off"
        self.notify(f"flash: {state}", timeout=2)

    def action_toggle_drawer(self) -> None:
        if self._main is not None:
            self._main.toggle_detail_drawer()

    def action_toggle_org(self) -> None:
        if self._main is None:
            return
        self._main.toggle_org_drawer()
        # Kick a fresh probe on open so the sysmeter block isn't stale; the
        # periodic tick takes over from here.
        if self._main._top_drawer.is_open:
            self._refresh_sysmeter()

    def action_cycle_right_drawer(self) -> None:
        if self._main is None:
            return
        self._main.cycle_right_drawer()
        # Trigger probe refresh for whichever right drawer opened
        if self._main._system_drawer.is_open:
            self._refresh_system_probe()
        if self._main._network_drawer.is_open:
            self._refresh_ping()
            self._refresh_dns()

    def action_cycle_left_drawer(self) -> None:
        if self._main is None:
            return
        self._main.cycle_left_drawer()
        # Trigger AWS probe refresh when opening
        if self._main._aws_drawer.is_open:
            self._refresh_aws_probe()

    def action_cycle_top_drawer(self) -> None:
        """``w`` alias for toggle_org."""
        self.action_toggle_org()

    def action_toggle_system_drawer(self) -> None:
        """``s`` key: open/close the system drawer directly."""
        if self._main is None:
            return
        self._main.toggle_system_drawer()
        if self._main._system_drawer.is_open:
            self._refresh_system_probe()

    def action_toggle_network_drawer(self) -> None:
        """``n`` key: open/close the network/DNS drawer."""
        if self._main is None:
            return
        self._main.toggle_network_drawer()
        if self._main._network_drawer.is_open:
            self._refresh_ping()
            self._refresh_dns()

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
        self._save_prefs()

    def on_click(self, _event: events.Click) -> None:
        # Any click anywhere clears persistent effect sprites. Don't stop
        # the event -- card click handlers (selection, drilldown) still
        # need it. If no sprites are active this is a no-op.
        if self._main is not None:
            self._main.dismiss_all_effects()

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

    def action_toggle_errors(self) -> None:
        if self._main is not None:
            # Refresh content but don't auto-open -- the user is
            # explicitly toggling, so just honour the toggle.
            self._main._error_drawer.refresh_content(self.state)
            self._main.toggle_error_drawer()

    def action_clear_errors(self) -> None:
        if self._main is not None:
            self._main.clear_errors(self.state)

    def action_show_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_manage_repos(self) -> None:
        """Open the repo manager to enable/disable individual repos."""
        # Build the full list of known repo names (including disabled ones).
        known: set[str] = {r.full_name for r in self._repos}
        known |= set(self.state.health_by_name.keys())
        known |= self._disabled_repos
        if not known:
            self.notify("no repos known yet -- refresh first", severity="warning", timeout=3)
            return

        def _on_dismiss(disabled: set[str] | None) -> None:
            if disabled is None:
                return
            self._disabled_repos = disabled
            # Remove disabled repos from state so they vanish immediately.
            self.state.healths = [
                h for h in self.state.healths if h.status.full_name not in disabled
            ]
            self.state.health_by_name = {h.status.full_name: h for h in self.state.healths}
            self._rerender()
            self._save_prefs()
            n_disabled = len(disabled)
            label = f"{n_disabled} disabled" if n_disabled else "all enabled"
            self.notify(f"repos: {label}", timeout=2)

        self.push_screen(
            RepoManager(sorted(known), self._disabled_repos),
            callback=_on_dismiss,
        )

    def action_manage_orgs(self) -> None:
        """Open the org manager to enable/disable organizations."""
        if self._github_client is None:
            self.notify("no GitHub client -- cannot list orgs", severity="warning", timeout=3)
            return

        viewer = get_viewer_login(self._github_client)
        available = list_user_orgs(self._github_client)
        if not available:
            self.notify("no organizations found for this account", severity="warning", timeout=3)
            return

        def _on_dismiss(disabled: set[str] | None) -> None:
            if disabled is None:
                return
            self._disabled_orgs = disabled
            # Rebuild owners list: personal (first) + non-disabled orgs.
            new_owners = [self._owners[0]] if self._owners else []
            for org_login in available:
                if org_login not in disabled and org_login not in new_owners:
                    new_owners.append(org_login)
            self._owners = new_owners
            if self._main is not None:
                self._main._owners = new_owners
                self._main._status_bar._owners = new_owners
            self._save_prefs()
            # Trigger a full refresh to pick up repos from newly added orgs.
            n_disabled = len(disabled)
            label = f"{n_disabled} disabled" if n_disabled else "all enabled"
            self.notify(f"orgs: {label}", timeout=2)
            self._trigger_refresh()

        self.push_screen(
            OrgManager(available, self._disabled_orgs, viewer_login=viewer),
            callback=_on_dismiss,
        )

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

    def _open_deployment_by_label(self, label: str) -> None:
        """Open a deployment link by reserved label (``main`` or ``dev``)."""
        from .deployments import find_link, resolve_links

        health = selected_health(self.state)
        if health is None:
            return
        links = resolve_links(health.status)
        link = find_link(links, label)
        if link:
            webbrowser.open(link.url)
        else:
            self.notify(
                f"no url configured for {label} on {health.status.full_name}",
                severity="warning",
                timeout=3,
            )

    def _open_deployment_supplemental(self, index: int) -> None:
        """Open a supplemental link by 0-based index (after main/dev are excluded)."""
        from .deployments import resolve_links, sort_links_for_display

        health = selected_health(self.state)
        if health is None:
            return
        links = resolve_links(health.status)
        supplementals = [
            link for link in sort_links_for_display(links) if link.label not in ("main", "dev")
        ]
        if index < len(supplementals):
            webbrowser.open(supplementals[index].url)
        else:
            self.notify("no supplemental link at that position", severity="warning", timeout=3)

    def action_open_deployment_main(self) -> None:
        """``z`` -- open the main/prod deployment URL for the selected repo."""
        self._open_deployment_by_label("main")

    def action_open_deployment_dev(self) -> None:
        """``x`` -- open the dev/staging deployment URL for the selected repo."""
        self._open_deployment_by_label("dev")

    def action_open_deployment_3(self) -> None:
        """``c`` -- open the 1st supplemental link (after main/dev)."""
        self._open_deployment_supplemental(0)

    def action_open_deployment_4(self) -> None:
        """``v`` -- open the 2nd supplemental link."""
        self._open_deployment_supplemental(1)

    def action_open_deployment_5(self) -> None:
        """``b`` -- open the 3rd supplemental link."""
        self._open_deployment_supplemental(2)

    def action_manage_deployments(self) -> None:
        """``u`` -- open the manage-deployment-links modal for the selected repo."""
        from .screens.manage_deployments import ManageDeployments

        health = selected_health(self.state)
        if health is None:
            return
        self.push_screen(ManageDeployments(health.status.full_name))

    # ---- message handlers ----

    def on_repo_card_selected(self, message: RepoCard.Selected) -> None:
        self.state.selected_full_name = message.full_name
        self._rerender()

    def on_repo_card_drilldown_requested(self, message: RepoCard.DrilldownRequested) -> None:
        health = self.state.health_by_name.get(message.full_name)
        if health is not None:
            self.push_screen(DrillDownScreen(health))

    def on_repo_card_open_url(self, message: RepoCard.OpenUrl) -> None:
        webbrowser.open_new_tab(message.url)

    def on_repo_card_manage_deployments_requested(
        self,
        message: RepoCard.ManageDeploymentsRequested,
    ) -> None:
        from .screens.manage_deployments import ManageDeployments

        self.push_screen(ManageDeployments(message.full_name))

    def on_repo_card_go_back(self, _message: RepoCard.GoBack) -> None:
        if self._main is not None:
            for drawer in (
                self._main._top_drawer,
                self._main._drawer,
                self._main._system_drawer,
                self._main._network_drawer,
                self._main._error_drawer,
                self._main._aws_drawer,
            ):
                if drawer.is_open:
                    drawer.close()
                    return
        # Stack contains [default_screen, MainScreen, ...modal screens].
        # Only pop if there is a modal above MainScreen.
        if len(self.screen_stack) > 2:
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
    owners: list[str] | None = None,
    skip_refresh: bool = False,
    github_client: Github | None = None,
    auto_discover: bool = False,
    saved_prefs: DashboardPrefs | None = None,
) -> None:
    """Launch the v2 interactive dashboard."""
    app_cls = DashboardApp
    cur_theme = theme
    cur_layout = layout
    cur_prefs = saved_prefs

    while True:
        app = app_cls(
            repos=repos,
            refresh_seconds=refresh_seconds,
            initial_theme=cur_theme,
            initial_layout=cur_layout,
            health_config=health_config,
            org_name=org_name,
            owners=owners,
            skip_refresh=skip_refresh,
            github_client=github_client,
            auto_discover=auto_discover,
            saved_prefs=cur_prefs,
        )
        app.run()

        if not getattr(app, "_restart_requested", False):
            break

        # Purge all augint_tools modules so re-import picks up code changes.
        stale_mods = [k for k in sys.modules if k.startswith("augint_tools")]
        for k in stale_mods:
            del sys.modules[k]
        importlib.invalidate_caches()

        # Re-import fresh classes after the purge.
        fresh_app = importlib.import_module("augint_tools.dashboard.app")
        fresh_prefs = importlib.import_module("augint_tools.dashboard.prefs")
        app_cls = fresh_app.DashboardApp
        cur_prefs = fresh_prefs.load_prefs()
        cur_theme = cur_prefs.theme_name
        cur_layout = cur_prefs.layout_name
