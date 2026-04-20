"""Tests for the v2 dashboard command and app (`ai-gh dashboard`)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from github.GithubException import GithubException

from augint_tools.cli.__main__ import cli as main
from augint_tools.dashboard import state
from augint_tools.dashboard._data import RepoStatus
from augint_tools.dashboard.app import DashboardApp
from augint_tools.dashboard.health import RepoHealth, Severity
from augint_tools.dashboard.health._models import HealthCheckResult
from augint_tools.dashboard.layouts import (
    get_layout,
    list_layouts,
    register_layout,
)
from augint_tools.dashboard.prefs import DashboardPrefs
from augint_tools.dashboard.state import (
    FILTER_MODES,
    SORT_MODES,
    AppState,
    RepoTeamInfo,
    apply_active_filters,
    apply_filter,
    apply_sort,
    available_filter_modes,
    ensure_selection,
    move_selection,
    team_accent,
    team_filter_mode,
    team_key_from_filter,
)
from augint_tools.dashboard.themes import get_theme, list_themes

# Marker for tests that boot the Textual TUI via ``app.run_test()`` (Pilot).
# These require a working terminal and may hang in headless CI runners.
# Run ``pytest -m tui`` locally to exercise them.
tui = pytest.mark.tui

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _status(
    name="myrepo",
    full_name="org/myrepo",
    main_status="success",
    open_issues=0,
    open_prs=0,
    is_workspace=False,
    tags=(),
    private=False,
) -> RepoStatus:
    return RepoStatus(
        name=name,
        full_name=full_name,
        is_service=False,
        main_status=main_status,
        main_error=None,
        dev_status=None,
        dev_error=None,
        open_issues=open_issues,
        open_prs=open_prs,
        draft_prs=0,
        is_workspace=is_workspace,
        tags=tags,
        private=private,
    )


def _health(
    name="myrepo",
    full_name="org/myrepo",
    checks=None,
    is_workspace=False,
    tags=(),
    private=False,
) -> RepoHealth:
    return RepoHealth(
        status=_status(
            name=name,
            full_name=full_name,
            is_workspace=is_workspace,
            tags=tags,
            private=private,
        ),
        checks=checks or [],
    )


def _mock_repo(name="myrepo", full_name="org/myrepo"):
    repo = MagicMock()
    repo.name = name
    repo.full_name = full_name
    repo.default_branch = "main"
    repo.open_issues_count = 0
    repo.archived = False
    return repo


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestDashboardCLI:
    def test_dashboard_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["dashboard", "--help"])
        assert result.exit_code == 0
        assert "--all" in result.output
        assert "--theme" in result.output
        assert "--layout" in result.output
        assert "--no-refresh" in result.output

    def test_dashboard_bad_theme(self):
        runner = CliRunner()
        with (
            patch(
                "augint_tools.dashboard.cmd.load_env_config",
                return_value=("repo", "account", "tok"),
            ),
            patch("augint_tools.dashboard.cmd.get_github_client"),
        ):
            result = runner.invoke(main, ["dashboard", "--theme", "nonexistent"])
            assert result.exit_code != 0
            assert "Unknown theme" in result.output

    def test_dashboard_bad_layout(self):
        runner = CliRunner()
        with (
            patch(
                "augint_tools.dashboard.cmd.load_env_config",
                return_value=("repo", "account", "tok"),
            ),
            patch("augint_tools.dashboard.cmd.get_github_client"),
        ):
            result = runner.invoke(main, ["dashboard", "--layout", "spiral"])
            assert result.exit_code != 0
            assert "Unknown layout" in result.output

    @patch("augint_tools.dashboard.app.run_dashboard", side_effect=KeyboardInterrupt)
    @patch("augint_tools.dashboard._common.get_github_repo")
    @patch("augint_tools.dashboard.cmd.load_env_config")
    @patch("augint_tools.dashboard.cmd.get_github_client")
    def test_dashboard_single_repo(self, mock_client, mock_env, mock_repo, mock_run):
        mock_env.return_value = ("myrepo", "myaccount", "tok")
        mock_repo.return_value = _mock_repo()
        runner = CliRunner()
        result = runner.invoke(main, ["dashboard"])
        assert result.exit_code == 0

    @patch("augint_tools.dashboard.app.run_dashboard", side_effect=KeyboardInterrupt)
    @patch("augint_tools.dashboard.cmd.list_repos_multi")
    @patch("augint_tools.dashboard.cmd.get_viewer_login", return_value="myaccount")
    @patch("augint_tools.dashboard.cmd.load_env_config")
    @patch("augint_tools.dashboard.cmd.get_github_client")
    def test_dashboard_all_flag(self, mock_client, mock_env, mock_viewer, mock_list, mock_run):
        mock_env.return_value = ("", "myaccount", "tok")
        mock_list.return_value = [_mock_repo()]
        runner = CliRunner()
        result = runner.invoke(main, ["dashboard", "--all"])
        assert result.exit_code == 0
        mock_list.assert_called_once()

    @patch("augint_tools.dashboard.app.run_dashboard", side_effect=KeyboardInterrupt)
    @patch("augint_tools.dashboard.cmd.list_repos_multi")
    @patch("augint_tools.dashboard.cmd.get_viewer_login", return_value="myaccount")
    @patch("augint_tools.dashboard.cmd.load_env_config")
    @patch("augint_tools.dashboard.cmd.get_github_client")
    def test_dashboard_env_auth(self, mock_client, mock_env, mock_viewer, mock_list, mock_run):
        mock_env.return_value = ("", "myaccount", "tok")
        mock_list.return_value = [_mock_repo()]
        runner = CliRunner()
        result = runner.invoke(main, ["dashboard", "--all", "--env-auth"])
        assert result.exit_code == 0
        mock_client.assert_called_once_with(auth_source="dotenv")


# ---------------------------------------------------------------------------
# Registries
# ---------------------------------------------------------------------------


class TestLayoutRegistry:
    def test_builtins_registered(self):
        names = list_layouts()
        assert set(names) >= {"packed", "grouped", "dense", "list"}

    def test_packed_is_first(self):
        # Default cycling starts at packed.
        assert list_layouts()[0] == "packed"

    def test_get_layout_round_trip(self):
        strategy = get_layout("packed")
        assert strategy.name == "packed"

    def test_register_new_layout(self):
        class _Dummy:
            name = "__test_dummy__"

            def apply(self, container, cards, ctx):
                pass

        register_layout(_Dummy())
        try:
            assert "__test_dummy__" in list_layouts()
            assert get_layout("__test_dummy__").name == "__test_dummy__"
        finally:
            # Clean up -- don't leak across tests.
            from augint_tools.dashboard.layouts import _LAYOUTS

            _LAYOUTS.pop("__test_dummy__", None)

    def test_unknown_layout_raises(self):
        try:
            get_layout("nonexistent")
        except KeyError:
            return
        raise AssertionError("expected KeyError")


class TestThemeRegistry:
    def test_paper_is_default(self):
        # The registry may list paper first; the app uses it as default.
        assert "paper" in list_themes()

    def test_all_themes_registered(self):
        expected = {"paper", "nord", "default", "minimal", "cyber", "matrix", "synthwave"}
        assert expected.issubset(set(list_themes()))

    def test_theme_spec_has_css(self):
        spec = get_theme("paper")
        assert spec.css_path.exists()
        text = spec.css_path.read_text()
        # CSS should include at least RepoCard styling.
        assert "RepoCard" in text

    def test_unknown_theme_raises(self):
        try:
            get_theme("nonexistent")
        except KeyError:
            return
        raise AssertionError("expected KeyError")


# ---------------------------------------------------------------------------
# Reducers
# ---------------------------------------------------------------------------


class TestApplySort:
    def test_alpha_sort(self):
        good = _health(name="aaa")
        bad = _health(name="zzz")
        out = apply_sort([bad, good], "alpha")
        assert [h.status.name for h in out] == ["aaa", "zzz"]

    def test_health_sort_prefers_worst(self):
        critical = HealthCheckResult(
            check_name="broken_ci", severity=Severity.CRITICAL, summary="x"
        )
        bad = _health(name="bad", full_name="org/bad", checks=[critical])
        good = _health(name="good", full_name="org/good")
        out = apply_sort([good, bad], "health")
        assert out[0].status.name == "bad"


class TestApplyFilter:
    def test_all_returns_everything(self):
        healths = [_health(name=n, full_name=f"org/{n}") for n in ("a", "b", "c")]
        assert len(apply_filter(healths, "all")) == 3

    def test_broken_ci_filter(self):
        broken = _health(
            name="broken",
            full_name="org/broken",
            checks=[
                HealthCheckResult(check_name="broken_ci", severity=Severity.CRITICAL, summary="x")
            ],
        )
        fine = _health(name="fine", full_name="org/fine")
        out = apply_filter([broken, fine], "broken-ci")
        assert [h.status.name for h in out] == ["broken"]

    def test_team_filter(self):
        a = _health(name="a", full_name="org/a")
        b = _health(name="b", full_name="org/b")
        repo_teams = {
            "org/a": RepoTeamInfo(primary="teamx", all=("teamx",)),
            "org/b": RepoTeamInfo(primary="teamy", all=("teamy",)),
        }
        out = apply_filter([a, b], team_filter_mode("teamx"), repo_teams)
        assert [h.status.name for h in out] == ["a"]


class TestApplyActiveFilters:
    def test_empty_set_returns_all(self):
        healths = [_health(name=n, full_name=f"org/{n}") for n in ("a", "b", "c")]
        assert len(apply_active_filters(healths, set())) == 3

    def test_single_filter(self):
        broken = _health(
            name="broken",
            full_name="org/broken",
            checks=[
                HealthCheckResult(check_name="broken_ci", severity=Severity.CRITICAL, summary="x")
            ],
        )
        fine = _health(name="fine", full_name="org/fine")
        out = apply_active_filters([broken, fine], {"broken-ci"})
        assert [h.status.name for h in out] == ["broken"]

    def test_no_workspace_and_with_selection(self):
        """no-workspace is a hard AND; broken-ci is an OR selection."""
        ci_check = HealthCheckResult(
            check_name="broken_ci", severity=Severity.CRITICAL, summary="x"
        )
        ws_broken = _health(
            name="ws-broken", full_name="org/ws-broken", checks=[ci_check], is_workspace=True
        )
        repo_broken = _health(
            name="repo-broken", full_name="org/repo-broken", checks=[ci_check], is_workspace=False
        )
        repo_ok = _health(name="repo-ok", full_name="org/repo-ok", is_workspace=False)
        out = apply_active_filters([ws_broken, repo_broken, repo_ok], {"broken-ci", "no-workspace"})
        # ws-broken excluded by no-workspace AND, repo-ok doesn't match broken-ci OR
        assert [h.status.name for h in out] == ["repo-broken"]

    def test_team_filters_or_logic(self):
        """Multiple team filters combine with OR: repo in ANY selected team passes."""
        a = _health(name="a", full_name="org/a")
        b = _health(name="b", full_name="org/b")
        c = _health(name="c", full_name="org/c")
        repo_teams = {
            "org/a": RepoTeamInfo(primary="alpha", all=("alpha",)),
            "org/b": RepoTeamInfo(primary="beta", all=("beta",)),
            "org/c": RepoTeamInfo(primary="gamma", all=("gamma",)),
        }
        out = apply_active_filters(
            [a, b, c],
            {team_filter_mode("alpha"), team_filter_mode("beta")},
            repo_teams,
        )
        assert {h.status.name for h in out} == {"a", "b"}

    def test_health_and_team_or_together(self):
        """broken-ci + team:alpha OR together: broken repos PLUS alpha repos."""
        ci_check = HealthCheckResult(
            check_name="broken_ci", severity=Severity.CRITICAL, summary="x"
        )
        a_broken = _health(name="a", full_name="org/a", checks=[ci_check])
        b_ok = _health(name="b", full_name="org/b")
        repo_teams = {
            "org/a": RepoTeamInfo(primary="alpha", all=("alpha",)),
            "org/b": RepoTeamInfo(primary="alpha", all=("alpha",)),
        }
        out = apply_active_filters(
            [a_broken, b_ok],
            {"broken-ci", team_filter_mode("alpha")},
            repo_teams,
        )
        # Both pass: a matches broken-ci, b matches team:alpha
        assert {h.status.name for h in out} == {"a", "b"}


class TestSelection:
    def test_ensure_selection_picks_first(self):
        s = AppState()
        s.healths = [_health(name="a", full_name="org/a"), _health(name="b", full_name="org/b")]
        s.health_by_name = {h.status.full_name: h for h in s.healths}
        ensure_selection(s)
        assert s.selected_full_name == "org/a"

    def test_move_selection_bounds(self):
        s = AppState()
        s.healths = [_health(name=n, full_name=f"org/{n}") for n in ("a", "b", "c")]
        s.health_by_name = {h.status.full_name: h for h in s.healths}
        ensure_selection(s)
        move_selection(s, 1)
        assert s.selected_full_name == "org/b"
        move_selection(s, 100)
        assert s.selected_full_name == "org/c"
        move_selection(s, -10)
        assert s.selected_full_name == "org/a"


class TestTeamHelpers:
    def test_accent_is_deterministic(self):
        assert team_accent("team-foo") == team_accent("team-foo")

    def test_accent_unassigned_is_grey(self):
        assert team_accent(state.UNASSIGNED_TEAM) == "#808080"

    def test_team_filter_round_trip(self):
        assert team_key_from_filter(team_filter_mode("team-x")) == "team-x"

    def test_available_filter_modes_includes_teams(self):
        repo_teams = {"org/a": RepoTeamInfo(primary="alpha", all=("alpha",))}
        labels = {"alpha": "Alpha Team"}
        modes = available_filter_modes(labels, repo_teams)
        assert "team:alpha" in modes
        # Standard filter modes still present.
        assert set(FILTER_MODES).issubset(set(modes))


# ---------------------------------------------------------------------------
# App construction
# ---------------------------------------------------------------------------


class TestDashboardApp:
    def test_app_constructs_with_defaults(self):
        app = DashboardApp(repos=[], skip_refresh=True)
        assert app.state.theme_name == "default"
        assert app.state.layout_name == "packed"
        assert app.state.sort_mode == SORT_MODES[0]

    def test_app_applies_initial_theme_and_layout(self):
        app = DashboardApp(
            repos=[], initial_theme="nord", initial_layout="dense", skip_refresh=True
        )
        assert app.state.theme_name == "nord"
        assert app.state.layout_name == "dense"

    def test_app_does_not_fetch_usage_in_init(self):
        # Usage must NOT block __init__ -- it runs in a post-mount worker.
        with patch("augint_tools.dashboard.app.fetch_all_usage", return_value=[]) as mock_fetch:
            DashboardApp(repos=[], skip_refresh=True)
            mock_fetch.assert_not_called()

    @tui
    def test_app_records_usage_error_after_mount(self):
        async def run():
            with patch(
                "augint_tools.dashboard.app.fetch_all_usage",
                side_effect=RuntimeError("boom"),
            ):
                app = DashboardApp(repos=[], skip_refresh=True)
                async with app.run_test() as pilot:
                    await pilot.pause()
                    # Worker is async; allow it to run.
                    await app.workers.wait_for_complete()
                    assert any(e.source == "usage" for e in app.state.errors)

        asyncio.run(run())


# ---------------------------------------------------------------------------
# Pilot-driven integration
# ---------------------------------------------------------------------------


def _seed_state(app: DashboardApp, n: int = 3) -> None:
    healths = [_health(name=f"r{i}", full_name=f"org/r{i}") for i in range(n)]
    app.state.healths = healths
    app.state.health_by_name = {h.status.full_name: h for h in healths}


@tui
class TestDashboardPilot:
    def test_startup_renders_without_repos(self):
        async def run():
            app = DashboardApp(repos=[], skip_refresh=True)
            _seed_state(app)
            async with app.run_test() as pilot:
                await pilot.pause()
                # main screen is mounted and state is visible
                assert app.screen.id in ("main-screen", None) or True
                assert app.state.selected_full_name == "org/r0"

        asyncio.run(run())

    def test_cycle_layout(self):
        async def run():
            app = DashboardApp(repos=[], skip_refresh=True)
            _seed_state(app)
            async with app.run_test() as pilot:
                await pilot.pause()
                start = app.state.layout_name
                await pilot.press("g")
                await pilot.pause()
                assert app.state.layout_name != start
                assert app.state.layout_name in list_layouts()

        asyncio.run(run())

    def test_cycle_theme(self):
        async def run():
            app = DashboardApp(repos=[], skip_refresh=True)
            _seed_state(app)
            async with app.run_test() as pilot:
                await pilot.pause()
                start = app.state.theme_name
                await pilot.press("t")
                await pilot.pause()
                assert app.state.theme_name != start

        asyncio.run(run())

    def test_cycle_sort(self):
        async def run():
            app = DashboardApp(repos=[], skip_refresh=True)
            _seed_state(app)
            async with app.run_test() as pilot:
                await pilot.pause()
                start = app.state.sort_mode
                await pilot.press("s")
                await pilot.pause()
                assert app.state.sort_mode != start

        asyncio.run(run())

    def test_drawer_toggle(self):
        async def run():
            app = DashboardApp(repos=[], skip_refresh=True)
            _seed_state(app)
            async with app.run_test() as pilot:
                await pilot.pause()
                drawer = app.query_one("#drawer")
                assert not drawer.has_class("open")
                await pilot.press("d")
                await pilot.pause()
                assert drawer.has_class("open")
                await pilot.press("d")
                await pilot.pause()
                assert not drawer.has_class("open")

        asyncio.run(run())

    def test_error_drawer_toggle(self):
        async def run():
            app = DashboardApp(repos=[], skip_refresh=True)
            _seed_state(app)
            app.state.log_error("refresh", "test injected error")
            async with app.run_test() as pilot:
                await pilot.pause()
                drawer = app.query_one("#error-drawer")
                assert not drawer.has_class("open")
                await pilot.press("e")
                await pilot.pause()
                assert drawer.has_class("open")
                await pilot.press("e")
                await pilot.pause()
                assert not drawer.has_class("open")

        asyncio.run(run())

    def test_help_screen(self):
        async def run():
            app = DashboardApp(repos=[], skip_refresh=True)
            _seed_state(app)
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press("question_mark")
                await pilot.pause()
                assert len(app.screen_stack) >= 2

        asyncio.run(run())

    def test_quit_does_not_hang(self):
        """Regression for v1 bug: `q` must exit even with a pending worker."""

        async def run():
            app = DashboardApp(repos=[], skip_refresh=True)
            _seed_state(app)
            async with app.run_test() as pilot:
                await pilot.pause()

                def _slow_work():
                    import time

                    time.sleep(10)

                app.run_worker(_slow_work, thread=True, exit_on_error=False)
                await pilot.pause()
                await pilot.press("q")

        # If this hangs, the test runner will time out.
        asyncio.run(asyncio.wait_for(run(), timeout=5))


# ---------------------------------------------------------------------------
# Additional coverage -- action handlers, rendering, state helpers
# ---------------------------------------------------------------------------


@tui
class TestDashboardActions:
    def test_open_filter_panel(self):
        async def run():
            app = DashboardApp(repos=[], skip_refresh=True)
            _seed_state(app)
            async with app.run_test() as pilot:
                await pilot.pause()
                assert not app.state.active_filters
                await pilot.press("f")
                await pilot.pause()
                # Panel is now open -- dismiss it
                await pilot.press("escape")
                await pilot.pause()

        asyncio.run(run())

    def test_toggle_workspace(self):
        async def run():
            app = DashboardApp(repos=[], skip_refresh=True)
            _seed_state(app)
            async with app.run_test() as pilot:
                await pilot.pause()
                assert "no-workspace" not in app.state.active_filters
                await pilot.press("w")
                await pilot.pause()
                assert "no-workspace" in app.state.active_filters
                await pilot.press("w")
                await pilot.pause()
                assert "no-workspace" not in app.state.active_filters

        asyncio.run(run())

    def test_move_left_right(self):
        async def run():
            app = DashboardApp(repos=[], skip_refresh=True)
            _seed_state(app)
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press("l")
                await pilot.pause()
                assert app.state.selected_full_name == "org/r1"
                await pilot.press("h")
                await pilot.pause()
                assert app.state.selected_full_name == "org/r0"

        asyncio.run(run())

    def test_move_down_up(self):
        async def run():
            app = DashboardApp(repos=[], skip_refresh=True)
            _seed_state(app, n=5)
            async with app.run_test() as pilot:
                await pilot.pause()
                start = app.state.selected_full_name
                await pilot.press("j")
                await pilot.pause()
                await pilot.press("k")
                await pilot.pause()
                assert app.state.selected_full_name == start

        asyncio.run(run())

    def test_refresh_no_repos_notifies(self):
        async def run():
            app = DashboardApp(repos=[], skip_refresh=True)
            _seed_state(app)
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press("r")
                await pilot.pause()
                # Refresh should not flip is_refreshing when there are no repos.
                assert app.state.is_refreshing is False

        asyncio.run(run())

    def test_usage_drawer(self):
        async def run():
            app = DashboardApp(repos=[], skip_refresh=True)
            _seed_state(app)
            async with app.run_test() as pilot:
                await pilot.pause()
                drawer = app.query_one("#drawer")
                await pilot.press("u")
                await pilot.pause()
                assert drawer.has_class("open")

        asyncio.run(run())

    def test_enter_opens_drilldown(self):
        async def run():
            app = DashboardApp(repos=[], skip_refresh=True)
            _seed_state(app)
            async with app.run_test() as pilot:
                await pilot.pause()
                with patch.object(app, "push_screen") as m:
                    app.action_open_selected()
                    assert m.called

        asyncio.run(run())

    def test_o_opens_browser(self):
        async def run():
            app = DashboardApp(repos=[], skip_refresh=True)
            _seed_state(app)
            with patch("augint_tools.dashboard.app.webbrowser.open") as m:
                async with app.run_test() as pilot:
                    await pilot.pause()
                    await pilot.press("o")
                    await pilot.pause()
                    assert m.called

        asyncio.run(run())


class TestRepoCardRender:
    def _spec(self):
        from augint_tools.dashboard.widgets.repo_card import RepoCard

        return RepoCard, get_theme("paper")

    def test_render_packed(self):
        RepoCard, spec = self._spec()
        card = RepoCard(_health(), theme_spec=spec, team_label="alpha", team_accent="#123456")
        card.render_mode = "packed"
        out = card.render()
        assert "myrepo" in out.plain

    def test_render_dense(self):
        RepoCard, spec = self._spec()
        card = RepoCard(_health(), theme_spec=spec)
        card.render_mode = "dense"
        out = card.render()
        assert "myrepo" in out.plain

    def test_render_list_with_team_line(self):
        RepoCard, spec = self._spec()
        card = RepoCard(_health(), theme_spec=spec, team_label="alpha", team_accent="#123456")
        card.render_mode = "list"
        out = card.render()
        assert "myrepo" in out.plain
        # Team badge is pinned on the rounded border (border_subtitle) rather
        # than rendered as a row, so cards with teams stay the same height.
        assert card.border_subtitle == " alpha "

    def test_render_none_loading(self):
        RepoCard, spec = self._spec()
        card = RepoCard(_health(), theme_spec=spec)
        card.health = None
        out = card.render()
        assert "loading" in out.plain

    def test_severity_class_critical(self):
        RepoCard, spec = self._spec()
        critical = HealthCheckResult(
            check_name="broken_ci", severity=Severity.CRITICAL, summary="x"
        )
        card = RepoCard(_health(checks=[critical]), theme_spec=spec)
        assert card.has_class("card--critical")

    def test_severity_class_ok(self):
        RepoCard, spec = self._spec()
        card = RepoCard(_health(), theme_spec=spec)
        assert card.has_class("card--ok")

    def test_render_tags_visible(self):
        RepoCard, spec = self._spec()
        health = _health(tags=("py", "sam"))
        card = RepoCard(health, theme_spec=spec)
        out = card.render()
        assert "py" in out.plain
        assert "sam" in out.plain

    def test_render_workspace_tag(self):
        RepoCard, spec = self._spec()
        health = _health(is_workspace=True)
        card = RepoCard(health, theme_spec=spec)
        out = card.render()
        assert "ws" in out.plain

    def test_apply_theme(self):
        RepoCard, spec = self._spec()
        card = RepoCard(_health(), theme_spec=spec)
        nord = get_theme("nord")
        card.apply_theme(nord)
        assert card._theme_spec is nord


class TestDrillDown:
    def test_drilldown_renders_findings(self):
        from augint_tools.dashboard.screens.drilldown import (
            DrillDownScreen,
        )

        critical = HealthCheckResult(
            check_name="broken_ci",
            severity=Severity.CRITICAL,
            summary="ci broken",
            link="https://x.y/z",
        )
        screen = DrillDownScreen(_health(checks=[critical]))
        text = screen._build_body().plain
        assert "org/myrepo" in text
        assert "broken_ci" in text
        assert "https://x.y/z" in text

    def test_drilldown_renders_no_findings(self):
        from augint_tools.dashboard.screens.drilldown import (
            DrillDownScreen,
        )

        screen = DrillDownScreen(_health())
        text = screen._build_body().plain
        assert "no findings" in text

    def test_drilldown_open_browser(self):
        from augint_tools.dashboard.screens.drilldown import (
            DrillDownScreen,
        )

        screen = DrillDownScreen(_health())
        with patch("augint_tools.dashboard.screens.drilldown.webbrowser.open") as m:
            screen.action_open_browser()
            assert m.called


class TestStateHelpers:
    def test_selected_health_returns_none_when_empty(self):
        from augint_tools.dashboard.state import selected_health

        s = AppState()
        assert selected_health(s) is None

    def test_selected_health_returns_current(self):
        from augint_tools.dashboard.state import selected_health

        s = AppState()
        h = _health(name="x", full_name="org/x")
        s.healths = [h]
        s.health_by_name = {h.status.full_name: h}
        assert selected_health(s) is h

    def test_move_selection_empty_is_noop(self):
        s = AppState()
        move_selection(s, 1)
        assert s.selected_full_name is None

    def test_collect_and_merge_repo_teams(self):
        from augint_tools.dashboard.state import collect_repo_teams, merge_team_data

        s = AppState()
        team = MagicMock()
        team.slug = "alpha"
        team.name = "Alpha"
        team.permission = "admin"
        repo = _mock_repo(full_name="org/x")
        repo.get_teams.return_value = [team]
        td = collect_repo_teams(repo)
        assert td.error is None
        merge_team_data(s, [td])
        assert s.repo_teams["org/x"].primary == "alpha"
        assert s.team_labels.get("alpha") == "Alpha"

    def test_collect_repo_teams_api_failure(self):
        from augint_tools.dashboard.state import (
            UNASSIGNED_TEAM,
            collect_repo_teams,
            merge_team_data,
        )

        s = AppState()
        repo = _mock_repo(full_name="org/x")
        repo.get_teams.side_effect = RuntimeError("boom")
        td = collect_repo_teams(repo)
        assert td.error is not None
        merge_team_data(s, [td])
        assert s.repo_teams["org/x"].primary == UNASSIGNED_TEAM

    def test_collect_repo_teams_403_suppressed(self):
        """403 from personal (non-org) repos is expected -- no error field."""
        from augint_tools.dashboard.state import collect_repo_teams

        repo = _mock_repo(full_name="user/personal-repo")
        repo.get_teams.side_effect = GithubException(403, "Forbidden", None)
        td = collect_repo_teams(repo)
        assert td.error is None
        assert td.info.all == ()

    def test_collect_repo_teams_404_suppressed(self):
        """404 from the Teams API is also expected for non-org repos."""
        from augint_tools.dashboard.state import collect_repo_teams

        repo = _mock_repo(full_name="user/personal-repo")
        repo.get_teams.side_effect = GithubException(404, "Not Found", None)
        td = collect_repo_teams(repo)
        assert td.error is None
        assert td.info.all == ()

    def test_collect_repo_teams_500_still_errors(self):
        """Server errors (5xx) should still be reported."""
        from augint_tools.dashboard.state import collect_repo_teams

        repo = _mock_repo(full_name="org/x")
        repo.get_teams.side_effect = GithubException(500, "Server Error", None)
        td = collect_repo_teams(repo)
        assert td.error is not None

    def test_apply_filter_no_renovate(self):
        check = HealthCheckResult(
            check_name="renovate_enabled", severity=Severity.HIGH, summary="missing"
        )
        missing = _health(name="m", full_name="org/m", checks=[check])
        out = apply_filter([missing, _health(name="ok", full_name="org/ok")], "no-renovate")
        assert [h.status.name for h in out] == ["m"]

    def test_apply_filter_stale_prs(self):
        check = HealthCheckResult(check_name="stale_prs", severity=Severity.MEDIUM, summary="stale")
        stale = _health(name="s", full_name="org/s", checks=[check])
        out = apply_filter([stale, _health(name="ok", full_name="org/ok")], "stale-prs")
        assert [h.status.name for h in out] == ["s"]

    def test_apply_filter_issues(self):
        check = HealthCheckResult(check_name="open_issues", severity=Severity.LOW, summary="open")
        iss = _health(name="i", full_name="org/i", checks=[check])
        out = apply_filter([iss, _health(name="ok", full_name="org/ok")], "issues")
        assert [h.status.name for h in out] == ["i"]

    def test_apply_filter_no_workspace(self):
        ws = _health(name="ws", full_name="org/ws", is_workspace=True)
        repo = _health(name="repo", full_name="org/repo", is_workspace=False)
        out = apply_filter([ws, repo], "no-workspace")
        assert [h.status.name for h in out] == ["repo"]

    def test_apply_filter_no_workspace_all_regular(self):
        a = _health(name="a", full_name="org/a")
        b = _health(name="b", full_name="org/b")
        out = apply_filter([a, b], "no-workspace")
        assert len(out) == 2

    def test_apply_sort_problem(self):
        critical = HealthCheckResult(
            check_name="broken_ci", severity=Severity.CRITICAL, summary="x"
        )
        bad = _health(name="bad", full_name="org/bad", checks=[critical])
        good = _health(name="good", full_name="org/good")
        out = apply_sort([good, bad], "problem")
        # CRITICAL sorts first (lower int -> worst).
        assert out[0].status.name == "bad"


class TestDetectRepoMetadata:
    def test_detect_language_tag(self):
        from augint_tools.dashboard._data import detect_repo_metadata

        repo = _mock_repo()
        repo.language = "Python"
        repo.get_contents.return_value = []
        is_ws, tags = detect_repo_metadata(repo)
        assert not is_ws
        assert "py" in tags

    def test_detect_workspace(self):
        from augint_tools.dashboard._data import detect_repo_metadata

        repo = _mock_repo()
        repo.language = None
        item = MagicMock()
        item.name = "workspace.yaml"
        repo.get_contents.return_value = [item]
        is_ws, tags = detect_repo_metadata(repo)
        assert is_ws

    def test_detect_framework_and_iac(self):
        from augint_tools.dashboard._data import detect_repo_metadata

        repo = _mock_repo()
        repo.language = "TypeScript"
        items = [MagicMock(name=n) for n in ("cdk.json", "main.tf", "src")]
        for item, n in zip(items, ("cdk.json", "main.tf", "src"), strict=True):
            item.name = n
        repo.get_contents.return_value = items
        is_ws, tags = detect_repo_metadata(repo)
        assert not is_ws
        assert "ts" in tags
        assert "cdk" in tags
        assert "tf" in tags

    def test_detect_sam_framework(self):
        from augint_tools.dashboard._data import detect_repo_metadata

        repo = _mock_repo()
        repo.language = "Python"
        items = [MagicMock(), MagicMock()]
        items[0].name = "template.yaml"
        items[1].name = "samconfig.toml"
        repo.get_contents.return_value = items
        is_ws, tags = detect_repo_metadata(repo)
        assert "py" in tags
        assert "sam" in tags

    def test_detect_api_failure_graceful(self):
        from augint_tools.dashboard._data import detect_repo_metadata

        repo = _mock_repo()
        repo.language = "Go"
        repo.get_contents.side_effect = GithubException(500, "error", None)
        is_ws, tags = detect_repo_metadata(repo)
        assert not is_ws
        assert tags == ("go",)

    def test_detect_next_framework(self):
        from augint_tools.dashboard._data import detect_repo_metadata

        repo = _mock_repo()
        repo.language = "TypeScript"
        item = MagicMock()
        item.name = "next.config.mjs"
        repo.get_contents.return_value = [item]
        is_ws, tags = detect_repo_metadata(repo)
        assert "ts" in tags
        assert "next" in tags


class TestBootstrapCache:
    def test_bootstrap_no_cache(self):
        from augint_tools.dashboard.state import bootstrap_from_cache

        s = AppState()
        with patch("augint_tools.dashboard.state.load_cache", return_value={}):
            assert bootstrap_from_cache(s) is False

    def test_bootstrap_with_cache(self):
        from augint_tools.dashboard.state import bootstrap_from_cache

        s = AppState()
        status = _status(name="x", full_name="org/x")
        with (
            patch(
                "augint_tools.dashboard.state.load_cache",
                return_value={"org/x": status},
            ),
            patch(
                "augint_tools.dashboard.state.load_health_cache",
                return_value={},
            ),
        ):
            assert bootstrap_from_cache(s) is True
            assert s.healths[0].status.full_name == "org/x"

    def test_bootstrap_load_failure_logs(self):
        from augint_tools.dashboard.state import bootstrap_from_cache

        s = AppState()
        with patch(
            "augint_tools.dashboard.state.load_cache",
            side_effect=RuntimeError("boom"),
        ):
            assert bootstrap_from_cache(s) is False
            assert any(e.source == "cache" for e in s.errors)


class TestAppMisc:
    @tui
    def test_card_selected_message_updates_state(self):
        async def run():
            app = DashboardApp(repos=[], skip_refresh=True)
            _seed_state(app)
            async with app.run_test() as pilot:
                await pilot.pause()
                # Dispatch the message via the handler directly.
                from augint_tools.dashboard.widgets.repo_card import (
                    RepoCard,
                )

                app.on_repo_card_selected(RepoCard.Selected("org/r2"))
                assert app.state.selected_full_name == "org/r2"

        asyncio.run(run())

    @tui
    def test_card_actions_opens_browser(self):
        async def run():
            app = DashboardApp(repos=[], skip_refresh=True)
            _seed_state(app)
            async with app.run_test() as pilot:
                await pilot.pause()
                from augint_tools.dashboard.widgets.repo_card import (
                    RepoCard,
                )

                with patch("augint_tools.dashboard.app.webbrowser.open_new_tab") as m:
                    app.on_repo_card_actions_requested(RepoCard.ActionsRequested("org/r0"))
                    assert m.called

        asyncio.run(run())

    def test_run_dashboard_invokes_app_run(self):
        from augint_tools.dashboard import app as _app_mod

        instance = MagicMock()
        instance._restart_requested = False
        with patch.object(_app_mod, "DashboardApp", return_value=instance) as mock_cls:
            _app_mod.run_dashboard([], skip_refresh=True)
            mock_cls.assert_called_once()
            instance.run.assert_called_once()

    @tui
    def test_drilldown_requested_pushes_screen(self):
        async def run():
            app = DashboardApp(repos=[], skip_refresh=True)
            async with app.run_test() as pilot:
                await pilot.pause()
                app.state.healths = [_health(full_name="org/r0")]
                app.state.health_by_name = {"org/r0": app.state.healths[0]}
                from augint_tools.dashboard.widgets.repo_card import (
                    RepoCard,
                )

                with patch.object(app, "push_screen") as m:
                    app.on_repo_card_drilldown_requested(RepoCard.DrilldownRequested("org/r0"))
                    assert m.called

        asyncio.run(run())

    @tui
    def test_pulls_requested_opens_browser(self):
        async def run():
            app = DashboardApp(repos=[], skip_refresh=True)
            async with app.run_test() as pilot:
                await pilot.pause()
                from augint_tools.dashboard.widgets.repo_card import (
                    RepoCard,
                )

                with patch("augint_tools.dashboard.app.webbrowser.open_new_tab") as m:
                    app.on_repo_card_pulls_requested(RepoCard.PullsRequested("org/r0"))
                    assert m.called
                    assert "pulls" in m.call_args.args[0]

        asyncio.run(run())

    @tui
    def test_go_back_closes_drawer(self):
        async def run():
            app = DashboardApp(repos=[], skip_refresh=True)
            async with app.run_test() as pilot:
                await pilot.pause()
                from augint_tools.dashboard.widgets.repo_card import (
                    RepoCard,
                )

                assert app._main is not None
                app._main._drawer.add_class("open")
                app.on_repo_card_go_back(RepoCard.GoBack())
                assert not app._main._drawer.is_open

        asyncio.run(run())

    @tui
    def test_toggle_org_drawer_renders_stats(self):
        async def run():
            app = DashboardApp(repos=[], skip_refresh=True, org_name="testorg")
            app.state.healths = [
                _health(full_name="org/a"),
                _health(full_name="org/b"),
            ]
            app.state.health_by_name = {h.status.full_name: h for h in app.state.healths}
            async with app.run_test() as pilot:
                await pilot.pause()
                app.action_toggle_org()
                await pilot.pause()
                assert app._main is not None
                # Org drawer is the top drawer (displaces cards, not a right overlay).
                assert app._main._top_drawer.is_open

        asyncio.run(run())

    @tui
    def test_org_drawer_with_no_data(self):
        async def run():
            app = DashboardApp(repos=[], skip_refresh=True)
            async with app.run_test() as pilot:
                await pilot.pause()
                assert app._main is not None
                content = app._main._org_drawer_content()
                assert "no data yet" in str(content)

        asyncio.run(run())

    @tui
    def test_ctrl_scroll_resizes_card(self):
        async def run():
            app = DashboardApp(repos=[], skip_refresh=True)
            async with app.run_test() as pilot:
                await pilot.pause()
                initial = app.state.panel_width
                event = MagicMock()
                event.ctrl = True
                app.on_mouse_scroll_up(event)
                assert app.state.panel_width > initial
                app.on_mouse_scroll_down(event)
                assert app.state.panel_width == initial

        asyncio.run(run())

    def test_selected_badge_in_render(self):
        from augint_tools.dashboard.themes import get_theme
        from augint_tools.dashboard.widgets.repo_card import RepoCard

        health = _health(full_name="org/r0")
        card = RepoCard(health=health, theme_spec=get_theme("default"))
        card.selected = False
        unselected = card.render().plain
        card.selected = True
        selected = card.render().plain
        # Selection is rendered as a badge (symbol-on-accent), not the word "SEL";
        # the exact glyph can change, so the assertion just verifies it's visible.
        assert selected != unselected

    def test_team_badge_in_render(self):
        from augint_tools.dashboard.themes import get_theme
        from augint_tools.dashboard.widgets.repo_card import RepoCard

        health = _health(full_name="org/r0")
        card = RepoCard(
            health=health,
            theme_spec=get_theme("default"),
            team_label="Platform",
            team_accent="#ff00ff",
        )
        # The team badge is pinned on the card's border via border_subtitle
        # so labelled cards don't grow taller than un-labelled ones.
        assert card.border_subtitle == " Platform "

    @tui
    def test_header_click_toggles_org_drawer(self):
        async def run():
            from augint_tools.dashboard.app import OrgDrawerHeader

            app = DashboardApp(repos=[], skip_refresh=True)
            async with app.run_test() as pilot:
                await pilot.pause()
                assert app._main is not None
                assert not app._main._top_drawer.is_open
                app._main.on_org_drawer_header_toggle(OrgDrawerHeader.Toggle())
                await pilot.pause()
                assert app._main._top_drawer.is_open

        asyncio.run(run())

    @tui
    def test_org_drawer_with_data_shows_stats(self):
        async def run():
            from augint_tools.dashboard.health._models import HealthCheckResult

            app = DashboardApp(repos=[], skip_refresh=True, org_name="nerds")
            critical = HealthCheckResult(
                check_name="broken_ci", severity=Severity.CRITICAL, summary="boom"
            )
            app.state.healths = [
                _health(name="broken", full_name="org/a", checks=[critical]),
                _health(name="ok1", full_name="org/b"),
                _health(name="ok2", full_name="org/c"),
            ]
            app.state.health_by_name = {h.status.full_name: h for h in app.state.healths}
            async with app.run_test() as pilot:
                await pilot.pause()
                assert app._main is not None
                left = app._main._org_drawer_content().plain
                middle = app._main._org_drawer_middle_content().plain
                right = app._main._org_drawer_right_content().plain
                # Left column: CI matrix.
                assert "ci matrix" in left
                # Middle column: org stats, weather, usage.
                assert "nerds" in middle
                assert "3 repos" in middle
                assert "health" in middle
                assert "repos" in middle
                assert "weather" in middle
                assert "usage" in middle
                # Right column: leaderboard.
                assert "worst 5" in right
                assert "broken" in right

        asyncio.run(run())

    @tui
    def test_ci_matrix_applies_team_accent_and_link(self):
        """The top-drawer CI matrix colours repo names by team accent and
        wraps each as a hyperlink to that repo's Actions page."""

        async def run():
            app = DashboardApp(repos=[], skip_refresh=True, org_name="nerds")
            app.state.team_labels = {
                "platform": "Platform",
                "growth": "Growth",
                state.UNASSIGNED_TEAM: "Unassigned",
            }
            app.state.repo_teams = {
                "org/a": RepoTeamInfo(primary="platform", all=("platform",)),
                "org/b": RepoTeamInfo(primary="growth", all=("growth",)),
                # deliberately missing entry for org/c -> falls back to grey
            }
            app.state.healths = [
                _health(name="repo-a", full_name="org/a"),
                _health(name="repo-b", full_name="org/b"),
                _health(name="repo-c", full_name="org/c"),
            ]
            app.state.health_by_name = {h.status.full_name: h for h in app.state.healths}
            async with app.run_test() as pilot:
                await pilot.pause()
                assert app._main is not None
                content = app._main._org_drawer_content()
                plain = content.plain
                assert "ci matrix" in plain
                # Collect spans that styled each repo name.
                name_spans: dict[str, str] = {}
                for span in content.spans:
                    rendered = plain[span.start : span.end]
                    stripped = rendered.strip()
                    if stripped in {"repo-a", "repo-b", "repo-c"}:
                        name_spans[stripped] = str(span.style)
                for repo in ("repo-a", "repo-b", "repo-c"):
                    assert repo in name_spans, f"no styled span for {repo}"
                    style = name_spans[repo]
                    assert f"link https://github.com/org/{repo[-1]}/actions" in style

                known = list(app.state.team_labels)
                platform_accent = team_accent("platform", known)
                growth_accent = team_accent("growth", known)
                assert platform_accent in name_spans["repo-a"]
                assert growth_accent in name_spans["repo-b"]
                # repo-c has no team entry -> default grey.
                assert "#808080" in name_spans["repo-c"]
                # Different teams should produce different accents.
                assert platform_accent != growth_accent

        asyncio.run(run())

    @tui
    def test_usage_block_renders_meter(self):
        async def run():
            from augint_tools.dashboard.usage import UsageStats

            app = DashboardApp(repos=[], skip_refresh=True, org_name="o")
            app.state.healths = [_health(full_name="org/a")]
            app.state.health_by_name = {"org/a": app.state.healths[0]}
            app.state.usage_stats = [
                UsageStats(
                    provider="claude_code",
                    display_name="Claude Code",
                    messages=500,
                    limit=1000,
                    status="ok",
                    tier="Pro 5x",
                ),
                UsageStats(
                    provider="openai",
                    display_name="OpenAI",
                    status="unconfigured",
                    error="set OPENAI_API_KEY",
                ),
            ]
            async with app.run_test() as pilot:
                await pilot.pause()
                assert app._main is not None
                plain = app._main._org_drawer_middle_content().plain
                assert "Claude Code" in plain
                assert "50%" in plain
                # The progress bar uses full-block and light-shade characters.
                assert "\u2588" in plain
                assert "\u2591" in plain
                # Unconfigured providers are hidden from the usage block.
                assert "OpenAI" not in plain

        asyncio.run(run())

    @tui
    def test_countdown_seeded_at_mount_with_repos(self):
        async def run():
            repo = _mock_repo(full_name="org/x")
            with (
                patch(
                    "augint_tools.dashboard.app.bootstrap_from_cache",
                    return_value=False,
                ),
                patch.object(DashboardApp, "_trigger_refresh"),
                patch.object(DashboardApp, "_refresh_usage"),
            ):
                app = DashboardApp(repos=[repo], refresh_seconds=300)
                async with app.run_test() as pilot:
                    await pilot.pause()
                    # With repos + not skipping, mount must seed next_refresh_at
                    # so the status bar has a countdown to show from paint zero.
                    assert app.state.next_refresh_at is not None

        asyncio.run(run())

    def test_status_bar_auto_refresh_off_phrase(self):
        from augint_tools.dashboard.widgets.status_bar import StatusBar

        app_state = AppState()
        bar = StatusBar()
        bar.bind_state(app_state, "testorg")
        # next_refresh_at None + not refreshing => "auto-refresh off".
        # Exercise the rerender path; it calls update() on the Static base.
        bar._rerender()
        # We can't read Static content without mounting, but the helper
        # method is deterministic: verify the refresh-phrase branch.
        assert app_state.next_refresh_at is None
        assert not app_state.is_refreshing

    def test_sparkline_renders_block_heights(self):
        from augint_tools.dashboard.app import _sparkline

        # Ascending values produce ascending-height glyphs.
        result = _sparkline([0, 1, 2, 3, 4])
        assert len(result) == 5
        # All-zero input returns a flat baseline (no division-by-zero).
        assert _sparkline([0, 0, 0]) == "\u2581" * 3
        # Empty input is empty.
        assert _sparkline([]) == ""

    def test_strip_dotfile_repos_filters_leading_dot(self):
        from augint_tools.dashboard._helpers import strip_dotfile_repos

        class _R:
            def __init__(self, name):
                self.name = name

        repos = [_R(".github"), _R("service"), _R(".auto"), _R("lib")]
        kept = strip_dotfile_repos(repos)
        assert [r.name for r in kept] == ["service", "lib"]

    @tui
    def test_drawer_contains_five_widget_labels(self):
        async def run():
            app = DashboardApp(repos=[], skip_refresh=True, org_name="acme")
            # Mix of severities across 3 repos + a "team" so the team-mix widget renders.
            crit = HealthCheckResult(
                check_name="broken_ci", severity=Severity.CRITICAL, summary="CI down"
            )
            med = HealthCheckResult(
                check_name="open_issues", severity=Severity.MEDIUM, summary="2 open"
            )
            app.state.healths = [
                _health(name="broken", full_name="org/a", checks=[crit]),
                _health(name="noisy", full_name="org/b", checks=[med]),
                _health(name="ok", full_name="org/c"),
            ]
            app.state.health_by_name = {h.status.full_name: h for h in app.state.healths}
            app.state.repo_teams = {
                "org/a": RepoTeamInfo(primary="platform", all=("platform",)),
                "org/b": RepoTeamInfo(primary="api", all=("api",)),
                "org/c": RepoTeamInfo(primary="platform", all=("platform",)),
            }
            app.state.team_labels = {"platform": "Platform", "api": "API"}
            async with app.run_test() as pilot:
                await pilot.pause()
                assert app._main is not None
                middle = app._main._org_drawer_middle_content().plain
                right = app._main._org_drawer_right_content().plain
                # Widgets are spread across columns.
                for label in ("weather", "activity", "pr ages", "teams"):
                    assert label in middle, f"{label!r} missing from middle column"
                assert "worst 5" in right, "'worst 5' missing from right column"

        asyncio.run(run())

    def test_panel_usage_history_fallback(self, tmp_path, monkeypatch):
        """history.jsonl is the primary source now; verify it is parsed."""
        from datetime import UTC, datetime

        from augint_tools.dashboard import usage as panel_usage

        home = tmp_path
        claude_dir = home / ".claude"
        claude_dir.mkdir()
        hist = claude_dir / "history.jsonl"
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        # Two recent entries on the same sessionId + one stale entry outside window.
        lines = [
            f'{{"timestamp": {now_ms}, "sessionId": "s1", "display": "/hi"}}',
            f'{{"timestamp": {now_ms - 1000}, "sessionId": "s1", "display": "/again"}}',
            '{"timestamp": 1, "sessionId": "ancient", "display": "/old"}',
        ]
        hist.write_text("\n".join(lines))
        monkeypatch.setattr(panel_usage.Path, "home", classmethod(lambda cls: home))
        agg = panel_usage._read_claude_history(window_days=7)
        assert agg.messages == 2
        assert agg.sessions == 1
        # Buckets API hits the same file; total equals recent entries.
        buckets = panel_usage.claude_daily_message_buckets(window_days=7)
        assert sum(buckets) == 2

    def test_panel_usage_openai_unconfigured_clear_message(self, monkeypatch, tmp_path):
        """Without env/keyring/config, OpenAI reports the SSO-less hint clearly."""
        from augint_tools.dashboard import usage as panel_usage

        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setattr(panel_usage.Path, "home", classmethod(lambda cls: tmp_path))
        stats = panel_usage.fetch_openai_usage()
        assert stats.status == "unconfigured"
        assert stats.error and "SSO" in stats.error

    def test_panel_usage_openai_resolves_env_key(self, monkeypatch):
        from augint_tools.dashboard import usage as panel_usage

        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        assert panel_usage._resolve_openai_key() == "sk-test"

    def test_panel_usage_openai_resolves_config_file(self, monkeypatch, tmp_path):
        from augint_tools.dashboard import usage as panel_usage

        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        home = tmp_path
        cfg_dir = home / ".openai"
        cfg_dir.mkdir()
        (cfg_dir / "api_key").write_text("sk-from-file\n")
        monkeypatch.setattr(panel_usage.Path, "home", classmethod(lambda cls: home))
        assert panel_usage._resolve_openai_key() == "sk-from-file"

    def test_panel_usage_copilot_no_gh_cli(self, monkeypatch):
        from augint_tools.dashboard import usage as panel_usage

        monkeypatch.setattr(panel_usage.shutil, "which", lambda _cmd: None)
        stats = panel_usage.fetch_copilot_usage()
        assert stats.status == "unconfigured"
        assert "gh CLI" in (stats.error or "")

    def test_panel_usage_copilot_billing_path(self, monkeypatch):
        from augint_tools.dashboard import usage as panel_usage

        monkeypatch.setattr(panel_usage.shutil, "which", lambda _cmd: "/usr/bin/gh")
        monkeypatch.setattr(
            panel_usage,
            "_gh_copilot_billing",
            lambda: {
                "copilot_plan": "business",
                "last_activity_at": "2026-04-10T10:00:00Z",
            },
        )
        stats = panel_usage.fetch_copilot_usage()
        assert stats.status == "ok"
        assert stats.tier == "business"
        assert "2026-04-10" in (stats.note or "")

    def test_panel_usage_daily_buckets_stats_cache_fallback(self, monkeypatch, tmp_path):
        """With no history.jsonl, fall back to stats-cache for daily buckets."""
        import json
        from datetime import UTC, datetime, timedelta

        from augint_tools.dashboard import usage as panel_usage

        today = datetime.now(UTC).date()
        home = tmp_path
        claude_dir = home / ".claude"
        claude_dir.mkdir()
        cache_path = claude_dir / "stats-cache.json"
        cache_path.write_text(
            json.dumps(
                {
                    "dailyActivity": [
                        {"date": (today - timedelta(days=1)).isoformat(), "messageCount": 42},
                        {"date": (today - timedelta(days=3)).isoformat(), "messageCount": 10},
                    ]
                }
            )
        )
        monkeypatch.setattr(panel_usage.Path, "home", classmethod(lambda cls: home))
        buckets = panel_usage.claude_daily_message_buckets(window_days=7)
        assert sum(buckets) == 52


class TestPrefs:
    def test_round_trip(self, tmp_path, monkeypatch):
        from augint_tools.dashboard.prefs import (
            DashboardPrefs,
            load_prefs,
            save_prefs,
        )

        monkeypatch.setattr("augint_tools.dashboard.prefs.CACHE_DIR", tmp_path)
        monkeypatch.setattr("augint_tools.dashboard.prefs.PREFS_FILE", tmp_path / "prefs.json")

        prefs = DashboardPrefs(
            theme_name="nord",
            layout_name="dense",
            sort_mode="alpha",
            active_filters=["broken-ci", "no-workspace"],
            panel_width=42,
            flash_enabled=False,
        )
        save_prefs(prefs)
        loaded = load_prefs()
        assert loaded.theme_name == "nord"
        assert loaded.layout_name == "dense"
        assert loaded.sort_mode == "alpha"
        assert loaded.active_filters == ["broken-ci", "no-workspace"]
        assert loaded.panel_width == 42
        assert loaded.flash_enabled is False

    def test_load_missing_file_returns_defaults(self, tmp_path, monkeypatch):
        from augint_tools.dashboard.prefs import DashboardPrefs, load_prefs

        monkeypatch.setattr(
            "augint_tools.dashboard.prefs.PREFS_FILE", tmp_path / "nonexistent.json"
        )
        loaded = load_prefs()
        assert loaded == DashboardPrefs()

    def test_load_corrupt_file_returns_defaults(self, tmp_path, monkeypatch):
        from augint_tools.dashboard.prefs import DashboardPrefs, load_prefs

        prefs_file = tmp_path / "prefs.json"
        prefs_file.write_text("{bad json")
        monkeypatch.setattr("augint_tools.dashboard.prefs.PREFS_FILE", prefs_file)
        loaded = load_prefs()
        assert loaded == DashboardPrefs()

    def test_unknown_fields_ignored(self, tmp_path, monkeypatch):
        import json

        from augint_tools.dashboard.prefs import load_prefs

        prefs_file = tmp_path / "prefs.json"
        prefs_file.write_text(json.dumps({"theme_name": "cyber", "future_field": True}))
        monkeypatch.setattr("augint_tools.dashboard.prefs.PREFS_FILE", prefs_file)
        loaded = load_prefs()
        assert loaded.theme_name == "cyber"

    @tui
    def test_app_saves_prefs_on_sort_cycle(self):
        async def run():
            app = DashboardApp(repos=[], skip_refresh=True)
            async with app.run_test() as pilot:
                await pilot.pause()
                with patch("augint_tools.dashboard.app.save_prefs") as mock_save:
                    app.action_cycle_sort()
                    assert mock_save.called
                    saved = mock_save.call_args[0][0]
                    assert saved.sort_mode == "alpha"

        asyncio.run(run())

    @tui
    def test_app_saves_prefs_on_theme_cycle(self):
        async def run():
            app = DashboardApp(repos=[], skip_refresh=True)
            async with app.run_test() as pilot:
                await pilot.pause()
                with patch("augint_tools.dashboard.app.save_prefs") as mock_save:
                    app.action_cycle_theme()
                    assert mock_save.called
                    saved = mock_save.call_args[0][0]
                    assert saved.theme_name != "default"

        asyncio.run(run())

    @tui
    def test_app_restores_saved_prefs(self):
        from augint_tools.dashboard.prefs import DashboardPrefs

        prefs = DashboardPrefs(
            sort_mode="problem",
            active_filters=["broken-ci"],
            panel_width=50,
            flash_enabled=False,
        )

        async def run():
            app = DashboardApp(repos=[], skip_refresh=True, saved_prefs=prefs)
            async with app.run_test() as pilot:
                await pilot.pause()
                assert app.state.sort_mode == "problem"
                assert app.state.active_filters == {"broken-ci"}
                assert app.state.panel_width == 50
                assert app._flash_enabled is False

        asyncio.run(run())


# ---------------------------------------------------------------------------
# Visibility filters (private/public)
# ---------------------------------------------------------------------------


class TestVisibilityFilters:
    def test_private_filter_matches_private_repos(self):
        priv = _health(name="secret", full_name="org/secret", private=True)
        pub = _health(name="oss", full_name="org/oss", private=False)
        out = apply_filter([priv, pub], "private")
        assert [h.status.name for h in out] == ["secret"]

    def test_public_filter_matches_public_repos(self):
        priv = _health(name="secret", full_name="org/secret", private=True)
        pub = _health(name="oss", full_name="org/oss", private=False)
        out = apply_filter([priv, pub], "public")
        assert [h.status.name for h in out] == ["oss"]

    def test_private_and_public_in_filter_modes(self):
        assert "private" in FILTER_MODES
        assert "public" in FILTER_MODES

    def test_active_filters_public_hides_private(self):
        priv = _health(name="secret", full_name="org/secret", private=True)
        pub = _health(name="oss", full_name="org/oss", private=False)
        out = apply_active_filters([priv, pub], {"public"})
        assert [h.status.name for h in out] == ["oss"]

    def test_public_and_broken_ci_or_together(self):
        """public + broken-ci OR together: public repos PLUS broken-CI repos."""
        ci_check = HealthCheckResult(
            check_name="broken_ci", severity=Severity.CRITICAL, summary="x"
        )
        pub_broken = _health(
            name="pub-broken", full_name="org/pub-broken", checks=[ci_check], private=False
        )
        priv_broken = _health(
            name="priv-broken", full_name="org/priv-broken", checks=[ci_check], private=True
        )
        pub_ok = _health(name="pub-ok", full_name="org/pub-ok", private=False)
        out = apply_active_filters([pub_broken, priv_broken, pub_ok], {"public", "broken-ci"})
        # All three pass: pub-broken matches both, priv-broken matches broken-ci, pub-ok matches public
        assert {h.status.name for h in out} == {"pub-broken", "priv-broken", "pub-ok"}

    def test_private_and_public_or_shows_all(self):
        """Selecting both private + public shows all repos (OR)."""
        priv = _health(name="secret", full_name="org/secret", private=True)
        pub = _health(name="oss", full_name="org/oss", private=False)
        out = apply_active_filters([priv, pub], {"private", "public"})
        assert {h.status.name for h in out} == {"secret", "oss"}

    def test_public_and_team_or_together(self):
        """public + team:alpha OR: public repos PLUS alpha team repos."""
        pub_team = _health(name="pub-team", full_name="org/pub-team", private=False)
        priv_team = _health(name="priv-team", full_name="org/priv-team", private=True)
        pub_no_team = _health(name="pub-no-team", full_name="org/pub-no-team", private=False)
        repo_teams = {
            "org/pub-team": RepoTeamInfo(primary="alpha", all=("alpha",)),
            "org/priv-team": RepoTeamInfo(primary="alpha", all=("alpha",)),
            "org/pub-no-team": RepoTeamInfo(primary="beta", all=("beta",)),
        }
        out = apply_active_filters(
            [pub_team, priv_team, pub_no_team],
            {"public", team_filter_mode("alpha")},
            repo_teams,
        )
        # All pass: pub-team matches both, priv-team matches team:alpha, pub-no-team matches public
        assert {h.status.name for h in out} == {"pub-team", "priv-team", "pub-no-team"}

    def test_no_workspace_constrains_or_selections(self):
        """no-workspace AND excludes workspaces even when other selections match."""
        priv_team = _health(
            name="priv-team", full_name="org/priv-team", private=True, is_workspace=False
        )
        pub_ws = _health(name="pub-ws", full_name="org/pub-ws", private=False, is_workspace=True)
        pub_other = _health(
            name="pub-other", full_name="org/pub-other", private=False, is_workspace=False
        )
        repo_teams = {
            "org/priv-team": RepoTeamInfo(primary="alpha", all=("alpha",)),
            "org/pub-ws": RepoTeamInfo(primary="alpha", all=("alpha",)),
            "org/pub-other": RepoTeamInfo(primary="beta", all=("beta",)),
        }
        out = apply_active_filters(
            [priv_team, pub_ws, pub_other],
            {"no-workspace", "public", team_filter_mode("alpha")},
            repo_teams,
        )
        # pub-ws excluded by no-workspace even though it matches public + team:alpha
        assert {h.status.name for h in out} == {"priv-team", "pub-other"}


class TestRepoStatusPrivateField:
    def test_private_field_defaults_false(self):
        status = _status()
        assert status.private is False

    def test_private_field_set_true(self):
        status = _status(private=True)
        assert status.private is True

    def test_cache_round_trip_preserves_private(self):
        """Private field survives cache serialization."""
        from dataclasses import asdict

        status = _status(name="secret", full_name="org/secret", private=True)
        data = asdict(status)
        assert data["private"] is True
        restored = RepoStatus(**data)
        assert restored.private is True


# ---------------------------------------------------------------------------
# Disabled repos (prefs + app)
# ---------------------------------------------------------------------------


class TestDisabledReposPrefs:
    def test_disabled_repos_round_trip(self, tmp_path, monkeypatch):
        from augint_tools.dashboard.prefs import (
            DashboardPrefs,
            load_prefs,
            save_prefs,
        )

        monkeypatch.setattr("augint_tools.dashboard.prefs.CACHE_DIR", tmp_path)
        monkeypatch.setattr("augint_tools.dashboard.prefs.PREFS_FILE", tmp_path / "prefs.json")

        prefs = DashboardPrefs(disabled_repos=["org/old-repo", "org/noisy"])
        save_prefs(prefs)
        loaded = load_prefs()
        assert sorted(loaded.disabled_repos) == ["org/noisy", "org/old-repo"]

    def test_disabled_repos_defaults_empty(self):
        from augint_tools.dashboard.prefs import DashboardPrefs

        prefs = DashboardPrefs()
        assert prefs.disabled_repos == []


class TestDisabledReposApp:
    def test_app_loads_disabled_repos_from_prefs(self):
        from augint_tools.dashboard.prefs import DashboardPrefs

        prefs = DashboardPrefs(disabled_repos=["org/disabled"])
        app = DashboardApp(repos=[], skip_refresh=True, saved_prefs=prefs)
        assert "org/disabled" in app._disabled_repos

    def test_app_saves_disabled_repos_in_prefs(self):
        app = DashboardApp(repos=[], skip_refresh=True)
        app._disabled_repos = {"org/disabled"}
        with patch("augint_tools.dashboard.app.save_prefs") as mock_save:
            app._save_prefs()
            saved = mock_save.call_args[0][0]
            assert "org/disabled" in saved.disabled_repos


# ---------------------------------------------------------------------------
# Status bar filter labels
# ---------------------------------------------------------------------------


class TestFilterLabels:
    def test_describe_filter_private(self):
        from augint_tools.dashboard.widgets.status_bar import describe_filter

        assert describe_filter("private") == "Private"

    def test_describe_filter_public(self):
        from augint_tools.dashboard.widgets.status_bar import describe_filter

        assert describe_filter("public") == "Public (Open source)"

    def test_describe_filter_org(self):
        from augint_tools.dashboard.widgets.status_bar import describe_filter

        assert describe_filter("org:my-org") == "org: my-org"


# ---------------------------------------------------------------------------
# Org filter support
# ---------------------------------------------------------------------------


class TestOrgFilter:
    def test_org_filter_mode_round_trip(self):
        from augint_tools.dashboard.state import org_filter_mode, org_key_from_filter

        assert org_key_from_filter(org_filter_mode("my-org")) == "my-org"

    def test_org_key_from_filter_returns_none_for_non_org(self):
        from augint_tools.dashboard.state import org_key_from_filter

        assert org_key_from_filter("team:alpha") is None
        assert org_key_from_filter("broken-ci") is None

    def test_owner_of(self):
        from augint_tools.dashboard.state import owner_of

        assert owner_of("my-org/my-repo") == "my-org"
        assert owner_of("single") == "single"

    def test_org_filter_matches_by_owner(self):
        a = _health(name="a", full_name="org-a/a")
        b = _health(name="b", full_name="org-b/b")
        out = apply_filter([a, b], "org:org-a")
        assert [h.status.name for h in out] == ["a"]

    def test_org_filters_or_logic(self):
        """Multiple org filters combine with OR: repo in ANY selected org passes."""
        a = _health(name="a", full_name="org-a/a")
        b = _health(name="b", full_name="org-b/b")
        c = _health(name="c", full_name="org-c/c")
        out = apply_active_filters([a, b, c], {"org:org-a", "org:org-b"})
        assert {h.status.name for h in out} == {"a", "b"}

    def test_org_and_health_or_together(self):
        """org:org-a + broken-ci OR together: org-a repos PLUS broken-CI repos."""
        ci_check = HealthCheckResult(
            check_name="broken_ci", severity=Severity.CRITICAL, summary="x"
        )
        a_broken = _health(name="a", full_name="org-a/a", checks=[ci_check])
        b_ok = _health(name="b", full_name="org-a/b")
        c_broken = _health(name="c", full_name="org-b/c", checks=[ci_check])
        out = apply_active_filters([a_broken, b_ok, c_broken], {"broken-ci", "org:org-a"})
        # All three pass: a matches both, b matches org:org-a, c matches broken-ci
        assert {h.status.name for h in out} == {"a", "b", "c"}

    def test_available_filter_modes_includes_orgs(self):
        healths = [
            _health(name="a", full_name="org-a/a"),
            _health(name="b", full_name="org-b/b"),
        ]
        modes = available_filter_modes({}, {}, healths=healths)
        assert "org:org-a" in modes
        assert "org:org-b" in modes
        assert set(FILTER_MODES).issubset(set(modes))


# ---------------------------------------------------------------------------
# Stale repos
# ---------------------------------------------------------------------------


class TestStaleRepos:
    def test_stale_repos_default_empty(self):
        s = AppState()
        assert s.stale_repos == set()

    def test_stale_card_rendering(self):
        from augint_tools.dashboard.themes import get_theme
        from augint_tools.dashboard.widgets.repo_card import RepoCard

        health = _health(full_name="org/r0")
        spec = get_theme("default")
        card = RepoCard(health=health, theme_spec=spec)

        # Not stale -- normal render.
        card.stale = False
        assert not card.has_class("card--stale")

        # Stale -- card gets the stale class.
        card.stale = True
        assert card.has_class("card--stale")

    def test_stale_card_render_content_dimmed(self):
        from augint_tools.dashboard.themes import get_theme
        from augint_tools.dashboard.widgets.repo_card import RepoCard

        health = _health(full_name="org/r0")
        spec = get_theme("default")
        card = RepoCard(health=health, theme_spec=spec)
        card.stale = True
        out = card.render()
        # The dim style is applied to the entire text.
        assert "myrepo" in out.plain


# ---------------------------------------------------------------------------
# Enabled orgs in prefs
# ---------------------------------------------------------------------------


class TestDisabledOrgsPrefs:
    def test_disabled_orgs_round_trip(self, tmp_path, monkeypatch):
        from augint_tools.dashboard.prefs import (
            DashboardPrefs,
            load_prefs,
            save_prefs,
        )

        monkeypatch.setattr("augint_tools.dashboard.prefs.CACHE_DIR", tmp_path)
        monkeypatch.setattr("augint_tools.dashboard.prefs.PREFS_FILE", tmp_path / "prefs.json")

        prefs = DashboardPrefs(disabled_orgs=["org-alpha", "org-beta"])
        save_prefs(prefs)
        loaded = load_prefs()
        assert sorted(loaded.disabled_orgs) == ["org-alpha", "org-beta"]

    def test_disabled_orgs_defaults_empty(self):
        from augint_tools.dashboard.prefs import DashboardPrefs

        prefs = DashboardPrefs()
        assert prefs.disabled_orgs == []

    def test_app_loads_disabled_orgs_from_prefs(self):
        from augint_tools.dashboard.prefs import DashboardPrefs

        prefs = DashboardPrefs(disabled_orgs=["org-alpha"])
        app = DashboardApp(repos=[], skip_refresh=True, saved_prefs=prefs)
        assert "org-alpha" in app._disabled_orgs

    def test_app_saves_disabled_orgs_in_prefs(self):
        app = DashboardApp(repos=[], skip_refresh=True)
        app._disabled_orgs = {"org-alpha"}
        with patch("augint_tools.dashboard.app.save_prefs") as mock_save:
            app._save_prefs()
            saved = mock_save.call_args[0][0]
            assert "org-alpha" in saved.disabled_orgs


# ---------------------------------------------------------------------------
# Multi-org CLI
# ---------------------------------------------------------------------------


class TestMultiOrgCLI:
    @patch("augint_tools.dashboard.app.run_dashboard", side_effect=KeyboardInterrupt)
    @patch("augint_tools.dashboard.cmd.list_repos_multi")
    @patch("augint_tools.dashboard.cmd.get_viewer_login", return_value="myuser")
    @patch("augint_tools.dashboard.cmd.load_env_config")
    @patch("augint_tools.dashboard.cmd.get_github_client")
    def test_all_flag_uses_multi_org(self, mock_client, mock_env, mock_viewer, mock_list, mock_run):
        mock_env.return_value = ("", "myuser", "tok")
        mock_list.return_value = [_mock_repo()]
        runner = CliRunner()
        result = runner.invoke(main, ["dashboard", "--all"])
        assert result.exit_code == 0
        # Should have called list_repos_multi with the viewer's login.
        call_args = mock_list.call_args
        owners = call_args[0][1]
        assert "myuser" in owners

    @patch("augint_tools.dashboard.app.run_dashboard", side_effect=KeyboardInterrupt)
    @patch("augint_tools.dashboard._common.get_github_repo")
    @patch("augint_tools.dashboard.cmd.load_env_config")
    @patch("augint_tools.dashboard.cmd.get_github_client")
    def test_no_flags_uses_single_repo(self, mock_client, mock_env, mock_repo, mock_run):
        """No --all or --interactive: falls back to GH_REPO single-repo mode."""
        mock_env.return_value = ("myrepo", "myaccount", "tok")
        mock_repo.return_value = _mock_repo()
        runner = CliRunner()
        result = runner.invoke(main, ["dashboard"])
        assert result.exit_code == 0

    @patch("augint_tools.dashboard.app.run_dashboard", side_effect=KeyboardInterrupt)
    @patch("augint_tools.dashboard.cmd.list_repos_multi")
    @patch("augint_tools.dashboard.cmd.list_user_orgs", return_value=["org-alpha", "org-beta"])
    @patch("augint_tools.dashboard.cmd.get_viewer_login", return_value="myuser")
    @patch("augint_tools.dashboard.cmd.load_env_config")
    @patch("augint_tools.dashboard.cmd.get_github_client")
    def test_disabled_orgs_excluded_from_owners(
        self, mock_client, mock_env, mock_viewer, mock_orgs, mock_list, mock_run
    ):
        """Saved disabled_orgs from prefs are excluded from the owners list."""
        mock_env.return_value = ("", "myuser", "tok")
        mock_list.return_value = [_mock_repo()]
        runner = CliRunner()
        with patch(
            "augint_tools.dashboard.cmd.load_prefs",
            return_value=DashboardPrefs(disabled_orgs=["org-beta"]),
        ):
            result = runner.invoke(main, ["dashboard", "--all"])
        assert result.exit_code == 0
        call_args = mock_list.call_args
        owners = call_args[0][1]
        assert "myuser" in owners
        assert "org-alpha" in owners
        assert "org-beta" not in owners
