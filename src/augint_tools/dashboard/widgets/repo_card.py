"""RepoCard -- one widget per repository.

Reactive fields (``health``, ``selected``, ``render_mode``, ``team_accent``)
drive re-render and CSS class updates. The card never reads global app
state directly; it is fed from the parent container. CSS transitions on
``border`` and ``background`` animate selection changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
    # Branch doesn't exist yet (e.g. main hasn't been cut off dev on a new
    # project). Shown in yellow so it stands out without reading as failure.
    "absent": "N/A",
}

# Upper bound for the subtle blue hint on the counts line. Matches the default
# ``open_issues_threshold`` -- at or above this, the open_issues health check
# flags the card and its severity styling already communicates the concern.
_ISSUE_HINT_THRESHOLD = 10

# An open issue older than this turns the issues count yellow so it stands
# out without escalating the whole card's severity.
_ISSUE_STALE_AGE = timedelta(days=3)


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


def _older_than(iso_ts: str | None, age: timedelta) -> bool:
    """True if ``iso_ts`` is set and strictly older than ``age``."""
    if not iso_ts:
        return False
    try:
        ts = datetime.fromisoformat(iso_ts)
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return datetime.now(UTC) - ts > age


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
        # "svc" is the union of both signals: a repo is service-flavoured if
        # it either runs a dev branch (gitflow-style) or carries a structural
        # marker like template.yaml/Dockerfile. Repos satisfying neither are
        # libraries.
        if health.status.has_dev_branch or health.status.looks_like_service:
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
        if status.has_dev_branch and status.dev_status is not None:
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
        spec = self._theme_spec
        line = Text()
        line.append("issues ")
        line.append(str(status.open_issues), style=self._issue_count_style(status, spec))
        line.append(f"  prs {status.open_prs}")
        if status.draft_prs:
            line.append(f" ({status.draft_prs}d)", style="dim")
        return line

    def _issue_count_style(self, status, spec: ThemeSpec) -> str:
        """Return the inline style for the issue count number.

        Yellow when any human-filed issue is older than three days; blue when
        there is at least one human-filed issue but the count is still below
        the health check's threshold. Otherwise no override -- inherit the
        card's default text colour.
        """
        if _older_than(status.oldest_issue_created_at, _ISSUE_STALE_AGE):
            return spec.severity_colors[Severity.MEDIUM]
        if 0 < status.human_open_issues < _ISSUE_HINT_THRESHOLD:
            return spec.severity_colors[Severity.LOW]
        return ""

    def _findings_lines(self, health: RepoHealth, limit: int | None) -> list[tuple[Text, str]]:
        """Return up to ``limit`` finding rows paired with their click-target URLs.

        ``limit=None`` (or any non-positive value) means no cap -- emit every
        finding. Per-row text is left full-length; Rich's ``no_wrap=True`` +
        ``overflow="ellipsis"`` (set in :meth:`_finalize`) trims at render time
        based on the card's actual width, which the user can grow or shrink
        with ctrl + mouse-wheel.

        When there are no real findings we still want the slot to carry useful
        signal rather than a flat "all green" string, so we emit OK-severity
        info lines summarising what work is sitting on this repo (open issues,
        open PRs, drafts). Each info line routes to the matching GitHub page
        so the user can click straight in to start working.
        """
        spec = self._theme_spec
        actions_url = self._actions_url(health)
        lines: list[tuple[Text, str]] = []

        def _at_cap() -> bool:
            return limit is not None and limit > 0 and len(lines) >= limit

        for error_label, error in (
            ("dev", health.status.dev_error),
            ("main", health.status.main_error),
        ):
            if error:
                t = Text()
                t.append(f"{error_label}: ", style="bold")
                t.append(error, style=spec.severity_colors[Severity.CRITICAL])
                lines.append((t, actions_url))
                if _at_cap():
                    return lines
        for finding in health.findings:
            t = Text()
            icon = spec.severity_icons.get(finding.severity, "*")
            t.append(f"{icon} ", style=self._severity_style(finding.severity, spec))
            t.append(finding.summary, style=self._severity_style(finding.severity, spec))
            lines.append((t, finding.link or self._repo_url(health)))
            if _at_cap():
                return lines
        if not lines:
            lines.extend(self._healthy_info_lines(health, limit))
        return lines

    def _healthy_info_lines(self, health: RepoHealth, limit: int | None) -> list[tuple[Text, str]]:
        """Informative OK-severity rows used when this repo has zero findings.

        Surfaces open issues / PRs / drafts with click targets, so a green card
        still offers a jumping-off point. Falls back to a single "healthy"
        line linking to the repo home when there's nothing to show at all.
        """
        spec = self._theme_spec
        status = health.status
        ok_style = spec.severity_colors[Severity.OK]
        full_name = status.full_name
        issues_url = f"https://github.com/{full_name}/issues"
        pulls_url = f"https://github.com/{full_name}/pulls"

        info: list[tuple[Text, str]] = []
        if status.open_issues > 0:
            noun = "issue" if status.open_issues == 1 else "issues"
            info.append(
                (
                    Text(f"{status.open_issues} open {noun}", style=ok_style),
                    issues_url,
                )
            )
        # "open PRs" here means non-draft PRs -- drafts get their own line so
        # the reader can tell at a glance which ones are actually review-ready.
        ready_prs = max(0, status.open_prs - status.draft_prs)
        if ready_prs > 0:
            noun = "pr" if ready_prs == 1 else "prs"
            info.append(
                (
                    Text(f"{ready_prs} open {noun}", style=ok_style),
                    pulls_url,
                )
            )
        if status.draft_prs > 0:
            noun = "draft" if status.draft_prs == 1 else "drafts"
            info.append(
                (
                    Text(f"{status.draft_prs} {noun}", style=ok_style),
                    pulls_url,
                )
            )
        if not info:
            info.append(
                (
                    Text("healthy", style=ok_style),
                    self._repo_url(health),
                )
            )
        if limit is None or limit <= 0:
            return info
        return info[:limit]

    def _render_packed(self, health: RepoHealth) -> Text:
        self._click_map = {}
        parts: list[Text] = []

        parts.append(self._title_line(health))
        self._click_map[len(parts)] = _ClickRegion(url=self._repo_url(health))

        parts.append(self._ci_line(health))
        self._click_map[len(parts)] = _ClickRegion(url=self._actions_url(health))

        parts.append(self._counts_line(health))
        self._click_map[len(parts)] = self._counts_click_region(health)

        for line, url in self._findings_lines(health, 3):
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

        # List mode is the verbose view -- no cap, every finding gets a row.
        # Rich still ellipsises individual lines that exceed the card width.
        for line, url in self._findings_lines(health, None):
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
        if status == "absent":
            # Branch not yet created -- informational yellow, not alerting.
            return "bold yellow"
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
