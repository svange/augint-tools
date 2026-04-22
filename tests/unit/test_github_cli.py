"""Tests for the gh CLI subprocess wrapper."""

from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from augint_tools.github import cli as gh_cli


@pytest.fixture(autouse=True)
def _clear_keyring_cache():
    gh_cli._keyring_works.cache_clear()
    yield
    gh_cli._keyring_works.cache_clear()


class TestRunGh:
    def test_strips_gh_token_when_keyring_works(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "dotenv-token")
        monkeypatch.setenv("GITHUB_TOKEN", "dotenv-github-token")
        monkeypatch.setenv("KEEP_ME", "yes")

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs.get("env")))
            if cmd == ["gh", "auth", "status"] and kwargs.get("env", {}).get("GH_TOKEN") is None:
                return CompletedProcess(args=cmd, returncode=0, stdout="")
            return CompletedProcess(args=cmd, returncode=0, stdout="ok")

        with patch("augint_tools.github.cli.subprocess.run", side_effect=fake_run):
            result = gh_cli.run_gh(["pr", "list"])

        assert result.returncode == 0
        pr_list_env = calls[-1][1]
        assert "GH_TOKEN" not in pr_list_env
        assert "GITHUB_TOKEN" not in pr_list_env
        assert pr_list_env.get("KEEP_ME") == "yes"

    def test_passes_through_gh_token_when_keyring_unavailable(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "dotenv-token")

        def fake_run(cmd, **kwargs):
            if cmd == ["gh", "auth", "status"]:
                return CompletedProcess(args=cmd, returncode=1, stdout="", stderr="not logged in")
            return CompletedProcess(args=cmd, returncode=0, stdout="ok")

        with patch("augint_tools.github.cli.subprocess.run", side_effect=fake_run) as mock_run:
            gh_cli.run_gh(["pr", "list"])

        pr_list_env = mock_run.call_args_list[-1].kwargs["env"]
        assert pr_list_env.get("GH_TOKEN") == "dotenv-token"

    def test_keyring_probe_is_cached(self, monkeypatch):
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

        with patch("augint_tools.github.cli.subprocess.run") as mock_run:
            mock_run.return_value = CompletedProcess(args=[], returncode=0, stdout="")
            gh_cli.run_gh(["pr", "list"])
            gh_cli.run_gh(["pr", "view", "1"])

        status_calls = [c for c in mock_run.call_args_list if c.args[0] == ["gh", "auth", "status"]]
        assert len(status_calls) == 1

    def test_keyring_probe_tolerates_missing_gh(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "dotenv-token")

        with patch(
            "augint_tools.github.cli.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            assert gh_cli._keyring_works() is False
