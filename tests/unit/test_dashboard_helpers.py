"""Tests for dashboard._helpers repo-selection + rate-limit helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import click
import pytest
from github.GithubException import GithubException, UnknownObjectException

from augint_tools.dashboard._helpers import (
    get_viewer_login,
    list_repos,
    list_repos_multi,
    list_user_orgs,
    select_org_interactive,
    select_repos_interactive,
    strip_dotfile_repos,
    warn_rate_limit,
)


def _repo(name: str, archived: bool = False, private: bool = False) -> MagicMock:
    r = MagicMock()
    r.name = name
    r.full_name = f"org/{name}"
    r.archived = archived
    r.private = private
    return r


class TestStripDotfileRepos:
    def test_drops_dotfile_repos(self):
        repos = [_repo("a"), _repo(".github"), _repo(".discussions"), _repo("b")]
        kept = strip_dotfile_repos(repos)
        assert [r.name for r in kept] == ["a", "b"]

    def test_handles_missing_name(self):
        weird = MagicMock(spec=[])  # no .name attribute
        assert strip_dotfile_repos([weird]) == [weird]


class TestListRepos:
    def test_organization_path(self):
        g = MagicMock()
        g.get_user.return_value.login = "viewer"
        g.get_organization.return_value.get_repos.return_value = [
            _repo("a"),
            _repo("archived", archived=True),
            _repo("b", private=True),
        ]
        repos = list_repos(g, "org")
        assert [r.name for r in repos] == ["a", "b"]
        g.get_organization.assert_called_with("org")

    def test_user_fallback_when_not_org(self):
        g = MagicMock()
        g.get_user.return_value.login = "viewer"
        g.get_organization.side_effect = UnknownObjectException(404, "nope", None)
        user = MagicMock()
        user.get_repos.return_value = [_repo("u1")]
        # Second get_user call takes the owner string.
        g.get_user.side_effect = [MagicMock(login="viewer"), user]
        repos = list_repos(g, "someuser")
        assert [r.name for r in repos] == ["u1"]

    def test_viewer_lookup_failure_swallowed(self):
        g = MagicMock()
        # First get_user() (for viewer) fails; second (for owner) succeeds.
        g.get_user.side_effect = [GithubException(500, "boom", None), MagicMock()]
        g.get_organization.return_value.get_repos.return_value = [_repo("a")]
        repos = list_repos(g, "org")
        assert [r.name for r in repos] == ["a"]

    def test_github_exception_on_org_lookup_falls_back_to_user(self):
        g = MagicMock()
        g.get_user.return_value.login = "viewer"
        g.get_organization.side_effect = GithubException(500, "boom", None)
        user = MagicMock()
        user.get_repos.return_value = [_repo("u1")]
        g.get_user.side_effect = [MagicMock(login="viewer"), user]
        repos = list_repos(g, "x")
        assert [r.name for r in repos] == ["u1"]


class TestSelectOrgInteractive:
    def test_no_orgs_returns_login(self):
        g = MagicMock()
        user = MagicMock()
        user.login = "me"
        user.get_orgs.return_value = []
        g.get_user.return_value = user
        assert select_org_interactive(g) == "me"

    def test_selects_org(self):
        g = MagicMock()
        user = MagicMock()
        user.login = "me"
        org_a = MagicMock()
        org_a.login = "acme"
        org_b = MagicMock()
        org_b.login = "beta"
        user.get_orgs.return_value = [org_a, org_b]
        g.get_user.return_value = user
        with patch("augint_tools.dashboard._helpers.click.prompt", return_value=1):
            assert select_org_interactive(g) == "acme"

    def test_selects_personal(self):
        g = MagicMock()
        user = MagicMock()
        user.login = "me"
        org = MagicMock()
        org.login = "acme"
        user.get_orgs.return_value = [org]
        g.get_user.return_value = user
        # Index 2 is the personal entry when one org is present.
        with patch("augint_tools.dashboard._helpers.click.prompt", return_value=2):
            assert select_org_interactive(g) == "me"

    def test_invalid_then_valid(self):
        g = MagicMock()
        user = MagicMock()
        user.login = "me"
        org = MagicMock()
        org.login = "acme"
        user.get_orgs.return_value = [org]
        g.get_user.return_value = user
        with patch("augint_tools.dashboard._helpers.click.prompt", side_effect=[9, 1]):
            assert select_org_interactive(g) == "acme"


class TestSelectReposInteractive:
    def test_empty_raises(self):
        with pytest.raises(click.ClickException):
            select_repos_interactive([])

    def test_valid_selection(self):
        repos = [_repo("a"), _repo("b"), _repo("c")]
        with patch("augint_tools.dashboard._helpers.click.prompt", return_value="1,3"):
            picked = select_repos_interactive(repos)
        assert [r.name for r in picked] == ["a", "c"]

    def test_invalid_then_valid(self):
        repos = [_repo("a"), _repo("b")]
        # First "99" yields nothing in range -> reprompt; then "1" returns one.
        with patch(
            "augint_tools.dashboard._helpers.click.prompt",
            side_effect=["99", "1"],
        ):
            picked = select_repos_interactive(repos)
        assert [r.name for r in picked] == ["a"]

    def test_non_integer_reprompted(self):
        repos = [_repo("a"), _repo("b")]
        with patch(
            "augint_tools.dashboard._helpers.click.prompt",
            side_effect=["bad", "2"],
        ):
            picked = select_repos_interactive(repos)
        assert [r.name for r in picked] == ["b"]


class TestViewerAndOrgHelpers:
    def test_get_viewer_login_success(self):
        g = MagicMock()
        g.get_user.return_value.login = "me"
        assert get_viewer_login(g) == "me"

    def test_get_viewer_login_failure(self):
        g = MagicMock()
        g.get_user.side_effect = GithubException(500, "boom", None)
        assert get_viewer_login(g) == ""

    def test_list_user_orgs_success(self):
        g = MagicMock()
        a = MagicMock()
        a.login = "a"
        b = MagicMock()
        b.login = "b"
        g.get_user.return_value.get_orgs.return_value = [a, b]
        assert list_user_orgs(g) == ["a", "b"]

    def test_list_user_orgs_failure(self):
        g = MagicMock()
        g.get_user.side_effect = GithubException(500, "boom", None)
        assert list_user_orgs(g) == []


class TestListReposMulti:
    def test_deduplicates_and_skips_errors(self):
        r_a = _repo("a")
        r_b = _repo("b")
        call_results = {
            "good": [r_a, r_b],
            "also_good": [r_a],  # duplicate full_name -> deduped
        }

        def fake_list(_g, owner):
            if owner == "bad":
                raise RuntimeError("boom")
            return call_results[owner]

        with patch("augint_tools.dashboard._helpers.list_repos", side_effect=fake_list):
            repos = list_repos_multi(MagicMock(), ["good", "bad", "also_good"])
        assert [r.full_name for r in repos] == ["org/a", "org/b"]


class TestWarnRateLimit:
    def test_zero_refresh_does_nothing(self, capsys):
        warn_rate_limit(repo_count=50, refresh_seconds=0)
        assert capsys.readouterr().out == ""

    def test_under_threshold_quiet(self, capsys):
        # 60s refresh with 10 repos: 60 refreshes/hr * 1 query = 60, quiet.
        warn_rate_limit(repo_count=10, refresh_seconds=60)
        assert "Warning" not in capsys.readouterr().out

    def test_over_threshold_warns(self, capsys):
        # 10s refresh * ~14 queries/refresh (350 repos) = 5040, very loud.
        warn_rate_limit(repo_count=350, refresh_seconds=10)
        out = capsys.readouterr().out
        assert "Warning" in out
        assert "GraphQL queries/hour" in out
