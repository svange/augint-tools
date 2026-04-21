"""RepoCard -- one widget per repository.

Reactive fields (``health``, ``selected``, ``render_mode``, ``team_accent``)
drive re-render and CSS class updates. The card never reads global app
state directly; it is fed from the parent container. CSS transitions on
``border`` and ``background`` animate selection changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from rich.text import Text
from textual import events
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget

from ..health import RepoHealth, Severity

if TYPE_CHECKING:
    from ..themes import ThemeSpec

RenderMode = Literal["packed", "dense", "list"]


@dataclass(frozen=True)
class _ClickRegion:
    """Where a middle-click on a given card row should navigate.

    ``x_split`` / ``alt_url`` let a single row route two different URLs
    based on click x -- used for the counts line so "issues N" opens
    /issues and "prs N" opens /pulls.
    """

    url: str
    x_split: int | None = None
    alt_url: str | None = None


_STATUS_ICON = {
    "success": "PASS",
    "failure": "FAIL",
    "in_progress": "RUN",
    "unknown": "? ",
}


def _within_window(iso_ts: str | None, window_seconds: int) -> bool:
    """True if ``iso_ts`` is set and less than ``window_seconds`` in the past."""
    if not iso_ts:
        return False
    try:
        ts = datetime.fromisoformat(iso_ts)
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    delta = (datetime.now(UTC) - ts).total_seconds()
    return 0 <= delta < window_seconds


def _truncate(value: str | None, width: int) -> str:
    if not value:
        return ""
    if len(value) <= width:
        return value
    return value[: max(0, width - 3)] + "..."


class RepoCard(Widget):
    """Reactive card for a single repository."""

    DEFAULT_CSS = """
    RepoCard {
        width: 38;
        border-subtitle-align: right;
    }
    """

    class Selected(Message):
        """Emitted when the card is selected (left click or focus)."""

        def __init__(self, full_name: str) -> None:
            super().__init__()
            self.full_name = full_name

    class DrilldownRequested(Message):
        """Emitted on left-click to open the drilldown screen."""

        def __init__(self, full_name: str) -> None:
            super().__init__()
            self.full_name = full_name

    class OpenUrl(Message):
        """Emitted on middle-click / meta+click to open a GitHub URL in a new tab.

        The URL is resolved per-row via the card's click map so that each
        piece of info (title, CI, counts, each finding) routes to the most
        actionable GitHub page for it.
        """

        def __init__(self, url: str) -> None:
            super().__init__()
            self.url = url

    class GoBack(Message):
        """Emitted on right-click -- app closes drawer or pops the top screen."""

    health: reactive[RepoHealth | None] = reactive(None)
    selected: reactive[bool] = reactive(False)
    stale: reactive[bool] = reactive(False)
    render_mode: reactive[RenderMode] = reactive("packed", layout=True)
    team_accent: reactive[str] = reactive("#808080")
    team_label: reactive[str] = reactive("")

    def __init__(
        self,
        health: RepoHealth,
        *,
        theme_spec: ThemeSpec,
        team_accent: str = "#808080",
        team_label: str = "",
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self._theme_spec = theme_spec
        self.health = health
        self.team_accent = team_accent
        self.team_label = team_label
        self.can_focus = True
        # Rebuilt on every render: y-coordinate inside the card -> click target.
        self._click_map: dict[int, _ClickRegion] = {}

    @property
    def repo_full_name(self) -> str:
        return self.health.status.full_name if self.health else ""

    # ---- reactive watchers ----

    def watch_selected(self, _old: bool, new: bool) -> None:
        if new:
            self.add_class("card--selected")
        else:
            self.remove_class("card--selected")
        self.refresh()

    def watch_stale(self, _old: bool, new: bool) -> None:
        if new:
            self.add_class("card--stale")
        else:
            self.remove_class("card--stale")
        self.refresh()

    def watch_team_accent(self, _old: str, new: str) -> None:
        # Colour the border-subtitle chip to match the team accent.
        try:
            self.styles.border_subtitle_color = new or "#808080"
        except Exception:
            pass
        self.refresh()

    def watch_team_label(self, _old: str, new: str) -> None:
        # The team badge lives on the bottom border rather than as a row in
        # the card body, so labelled cards don't grow taller than plain cards.
        self.border_subtitle = f" {new} " if new else ""
        self.refresh()

    def watch_health(self, _old: RepoHealth | None, new: RepoHealth | None) -> None:
        # Reactive setters call render() automatically; no manual refresh needed.
        self._apply_severity_class(new)

    def apply_theme(self, theme_spec: ThemeSpec) -> None:
        if self._theme_spec is theme_spec:
            return
        self._theme_spec = theme_spec
        self.refresh()

    def apply_flash_phase(self, phase: bool, *, window_seconds: int) -> None:
        """Toggle ``card--flash-on`` when this card is within the flash window.

        The card is "flashing" when it's critical or warning *and* the
        relevant transition (CI failure / warning_since) happened within
        ``window_seconds``. Outside that window the class is always removed
        so the border stays solid at its theme colour.
        """
        on = phase and self._is_recently_degraded(window_seconds)
        if on:
            self.add_class("card--flash-on")
        else:
            self.remove_class("card--flash-on")

    def _is_recently_degraded(self, window_seconds: int) -> bool:
        health = self.health
        if health is None:
            return False
        status = health.status
        # Critical: flash while any failing branch is within the window.
        if status.main_status == "failure" or status.dev_status == "failure":
            return _within_window(status.main_failing_since, window_seconds) or _within_window(
                status.dev_failing_since, window_seconds
            )
        # Warning: flash while the repo has only recently crossed into yellow.
        if "card--warning" in self.classes:
            return _within_window(health.warning_since, window_seconds)
        return False

    # ---- CSS classes ----

    def _apply_severity_class(self, health: RepoHealth | None) -> None:
        for cls in ("card--ok", "card--warning", "card--critical"):
            self.remove_class(cls)
        if health is None:
            return
        status = health.status
        if status.main_status == "failure" or status.dev_status == "failure":
            self.add_class("card--critical")
            return
        worst = health.worst_severity
        if worst == Severity.CRITICAL:
            self.add_class("card--critical")
        elif worst in (Severity.HIGH, Severity.MEDIUM):
            self.add_class("card--warning")
        else:
            self.add_class("card--ok")

    # ---- rendering ----

    def render(self) -> Text:
        if self.health is None:
            return Text("loading…", style="dim")
        if self.render_mode == "dense":
            result = self._render_dense(self.health)
        elif self.render_mode == "list":
            result = self._render_list(self.health)
        else:
            result = self._render_packed(self.health)
        if self.stale:
            result.stylize("dim")
        return result

    def _title_line(self, health: RepoHealth) -> Text:
        line = Text()
        if self.selected:
            line.append(" > ", style=f"bold black on {self._theme_spec.status_pass}")
            line.append(" ")
        line.append(health.status.name, style="bold")
        if health.status.is_service:
            line.append("  svc", style="dim")
        if health.status.is_workspace:
            line.append("  ws", style="dim")
        for tag in health.status.tags:
            line.append(f"  {tag}", style="dim italic")
        return line

    def _ci_line(self, health: RepoHealth) -> Text:
        spec = self._theme_spec
        status = health.status
        line = Text()
        if status.is_service and status.dev_status is not None:
            line.append("dev ")
            line.append(
                _STATUS_ICON.get(status.dev_status, "?"),
                style=self._status_style(status.dev_status, spec),
            )
            line.append("  main ")
            line.append(
                _STATUS_ICON.get(status.main_status, "?"),
                style=self._status_style(status.main_status, spec),
            )
        else:
            line.append("main ")
            line.append(
                _STATUS_ICON.get(status.main_status, "?"),
                style=self._status_style(status.main_status, spec),
            )
        return line

    def _counts_line(self, health: RepoHealth) -> Text:
        status = health.status
        line = Text()
        line.append(f"issues {status.open_issues}  prs {status.open_prs}")
        if status.draft_prs:
            line.append(f" ({status.draft_prs}d)", style="dim")
        return line

    def _findings_lines(self, health: RepoHealth, limit: int) -> list[tuple[Text, str]]:
        """Return up to ``limit`` finding rows paired with their click-target URLs."""
        spec = self._theme_spec
        actions_url = self._actions_url(health)
        lines: list[tuple[Text, str]] = []
        for error_label, error in (
            ("dev", health.status.dev_error),
            ("main", health.status.main_error),
        ):
            if error:
                t = Text()
                t.append(f"{error_label}: ", style="bold")
                t.append(_truncate(error, 30), style=spec.severity_colors[Severity.CRITICAL])
                lines.append((t, actions_url))
                if len(lines) >= limit:
                    return lines
        for finding in health.findings:
            t = Text()
            icon = spec.severity_icons.get(finding.severity, "*")
            t.append(f"{icon} ", style=self._severity_style(finding.severity, spec))
            t.append(
                _truncate(finding.summary, 40), style=self._severity_style(finding.severity, spec)
            )
            lines.append((t, finding.link or self._repo_url(health)))
            if len(lines) >= limit:
                return lines
        if not lines:
            lines.append(
                (
                    Text("all checks green", style=spec.severity_colors[Severity.OK]),
                    self._repo_url(health),
                )
            )
        return lines

    def _render_packed(self, health: RepoHealth) -> Text:
        self._click_map = {}
        parts: list[Text] = []

        parts.append(self._title_line(health))
        self._click_map[len(parts)] = _ClickRegion(url=self._repo_url(health))

        parts.append(self._ci_line(health))
        self._click_map[len(parts)] = _ClickRegion(url=self._actions_url(health))

        parts.append(self._counts_line(health))
        self._click_map[len(parts)] = self._counts_click_region(health)

        for line, url in self._findings_lines(health, 2):
            parts.append(line)
            self._click_map[len(parts)] = _ClickRegion(url=url)

        return self._finalize(parts)

    def _render_dense(self, health: RepoHealth) -> Text:
        self._click_map = {}
        parts: list[Text] = []

        parts.append(self._title_line(health))
        self._click_map[len(parts)] = _ClickRegion(url=self._repo_url(health))

        parts.append(self._ci_line(health))
        self._click_map[len(parts)] = _ClickRegion(url=self._actions_url(health))

        for line, url in self._findings_lines(health, 1):
            parts.append(line)
            self._click_map[len(parts)] = _ClickRegion(url=url)

        return self._finalize(parts)

    def _render_list(self, health: RepoHealth) -> Text:
        self._click_map = {}
        parts: list[Text] = []

        parts.append(self._title_line(health))
        self._click_map[len(parts)] = _ClickRegion(url=self._repo_url(health))

        parts.append(self._ci_line(health))
        self._click_map[len(parts)] = _ClickRegion(url=self._actions_url(health))

        parts.append(self._counts_line(health))
        self._click_map[len(parts)] = self._counts_click_region(health)

        for line, url in self._findings_lines(health, 4):
            parts.append(line)
            self._click_map[len(parts)] = _ClickRegion(url=url)

        return self._finalize(parts)

    def _finalize(self, parts: list[Text]) -> Text:
        """Join lines and mark the Text so Rich truncates with ... instead of wrapping.

        ``no_wrap=True`` + ``overflow="ellipsis"`` lets Rich trim any line that
        exceeds the card's render width. Without these, long titles/findings
        wrap onto extra rows and break the grid layout.
        """
        joined = Text("\n").join(parts)
        joined.no_wrap = True
        joined.overflow = "ellipsis"
        return joined

    # ---- URL + click-region helpers ----

    def _repo_url(self, health: RepoHealth) -> str:
        return f"https://github.com/{health.status.full_name}"

    def _actions_url(self, health: RepoHealth) -> str:
        return f"https://github.com/{health.status.full_name}/actions"

    def _content_start_x(self) -> int:
        """X coordinate of the first content cell inside the card.

        Widget coords include the border (1 cell) and horizontal padding
        (0 in dense mode, 1 otherwise) -- see the theme ``.tcss`` files.
        """
        return 1 if self.render_mode == "dense" else 2

    def _counts_click_region(self, health: RepoHealth) -> _ClickRegion:
        status = health.status
        full_name = status.full_name
        issues_url = f"https://github.com/{full_name}/issues"
        pulls_url = f"https://github.com/{full_name}/pulls"
        left_len = len(f"issues {status.open_issues}")
        # The line is "issues N  prs M..." -- split between the two spaces.
        split = self._content_start_x() + left_len + 1
        return _ClickRegion(url=issues_url, x_split=split, alt_url=pulls_url)

    def _severity_style(self, severity: Severity, spec: ThemeSpec) -> str:
        return spec.severity_colors.get(severity, spec.severity_colors[Severity.OK])

    def _status_style(self, status: str | None, spec: ThemeSpec) -> str:
        if status == "success":
            return f"bold {spec.status_pass}"
        if status == "failure":
            return f"bold {spec.status_fail}"
        if status == "in_progress":
            return f"bold {spec.status_running}"
        return f"bold {spec.status_unknown}"

    # ---- mouse + focus ----

    def on_click(self, event: events.Click) -> None:
        if event.button == 3:
            self.post_message(self.GoBack())
            return
        if event.button == 2 or (event.button == 1 and getattr(event, "meta", False)):
            url = self._resolve_click_url(event)
            self.post_message(self.OpenUrl(url))
            return
        if event.button == 1:
            # Single click only selects -- a plain click must never
            # trap the user in a modal drilldown screen.  Use a
            # double-click (chord), the Enter key, or 'o' to open full
            # detail; ``event.chain`` is 2 on the second click of a
            # double-click.
            self.post_message(self.Selected(self.repo_full_name))
            if getattr(event, "chain", 1) >= 2:
                self.post_message(self.DrilldownRequested(self.repo_full_name))

    def _resolve_click_url(self, event: events.Click) -> str:
        """Map the click's (x, y) to a GitHub URL using the current click map.

        Falls back to the repo's /actions page for clicks on the border or
        any row not present in the map (e.g. before the first render).
        """
        y = getattr(event, "y", 0)
        x = getattr(event, "x", 0)
        region = self._click_map.get(y)
        if region is None:
            return f"https://github.com/{self.repo_full_name}/actions"
        if region.x_split is not None and region.alt_url is not None and x >= region.x_split:
            return region.alt_url
        return region.url
