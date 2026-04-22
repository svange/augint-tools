"""Tests for dashboard._common env/auth helpers."""

from __future__ import annotations

import logging
import subprocess
from subprocess import CompletedProcess
from unittest.mock import MagicMock, patch

import pytest
from github.GithubException import UnknownObjectException
from loguru import logger

from augint_tools.dashboard._common import (
    _get_gh_cli_token,
    _load_dotenv_values,
    _resolve_token,
    configure_logging,
    get_github_client,
    get_github_repo,
    load_env_config,
)


@pytest.fixture(autouse=True)
def _reset_logging():
    # Keep loguru sinks from leaking across tests.
    yield
    logger.remove()
    logger.add(lambda _msg: None)


class TestLoadDotenvValues:
    def test_reads_values(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("FOO=bar\nBAZ=qux\n")
        values = _load_dotenv_values(".env")
        assert values == {"FOO": "bar", "BAZ": "qux"}

    def test_filters_none_values(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # An empty (no "=") line yields None as a value in python-dotenv.
        (tmp_path / ".env").write_text("FOO=bar\nNAKED\n")
        values = _load_dotenv_values(".env")
        assert values == {"FOO": "bar"}


class TestLoadEnvConfig:
    def test_environment_overrides_dotenv(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("GH_REPO=from-env\nGH_ACCOUNT=acct-env\nGH_TOKEN=tok-env\n")
        monkeypatch.setenv("GH_REPO", "from-os")
        monkeypatch.delenv("GH_ACCOUNT", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)
        repo, account, token = load_env_config(".env")
        assert repo == "from-os"
        assert account == "acct-env"
        assert token == "tok-env"

    def test_empty_fallbacks(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # No .env file and no env vars -> empty strings.
        monkeypatch.delenv("GH_REPO", raising=False)
        monkeypatch.delenv("GH_ACCOUNT", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)
        assert load_env_config(".env") == ("", "", "")


class TestGetGhCliToken:
    def test_strips_gh_tokens_from_env(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "narrow")
        monkeypatch.setenv("GITHUB_TOKEN", "narrow2")
        monkeypatch.setenv("KEEP_ME", "yes")
        captured_env = {}

        def fake_run(cmd, **kwargs):
            captured_env.update(kwargs.get("env") or {})
            return CompletedProcess(args=cmd, returncode=0, stdout="kr-token\n")

        with patch("augint_tools.dashboard._common.subprocess.run", side_effect=fake_run):
            assert _get_gh_cli_token() == "kr-token"
        assert "GH_TOKEN" not in captured_env
        assert "GITHUB_TOKEN" not in captured_env
        assert captured_env.get("KEEP_ME") == "yes"

    def test_returns_empty_on_called_process_error(self):
        with patch(
            "augint_tools.dashboard._common.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "gh"),
        ):
            assert _get_gh_cli_token() == ""

    def test_returns_empty_when_gh_missing(self):
        with patch("augint_tools.dashboard._common.subprocess.run", side_effect=FileNotFoundError):
            assert _get_gh_cli_token() == ""


class TestResolveToken:
    def test_dotenv_mode_returns_dotenv_token(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("GH_TOKEN=from-dotenv\n")
        assert _resolve_token(".env", auth_source="dotenv") == "from-dotenv"

    def test_dotenv_mode_raises_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("FOO=bar\n")
        with pytest.raises(RuntimeError, match="No GitHub token"):
            _resolve_token(".env", auth_source="dotenv")

    def test_unknown_auth_source_raises(self):
        with pytest.raises(ValueError, match="Unsupported"):
            _resolve_token(auth_source="bogus")

    def test_auto_prefers_gh_cli(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("GH_TOKEN=dotenv-tok\n")
        monkeypatch.setenv("GH_TOKEN", "env-tok")
        with patch("augint_tools.dashboard._common._get_gh_cli_token", return_value="kr-tok"):
            assert _resolve_token(".env") == "kr-tok"

    def test_auto_falls_back_to_env(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("GH_TOKEN=dotenv-tok\n")
        monkeypatch.setenv("GH_TOKEN", "env-tok")
        with patch("augint_tools.dashboard._common._get_gh_cli_token", return_value=""):
            assert _resolve_token(".env") == "env-tok"

    def test_auto_falls_back_to_dotenv(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("GH_TOKEN=dotenv-tok\n")
        monkeypatch.delenv("GH_TOKEN", raising=False)
        with patch("augint_tools.dashboard._common._get_gh_cli_token", return_value=""):
            assert _resolve_token(".env") == "dotenv-tok"

    def test_auto_raises_when_nothing_configured(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("GH_TOKEN", raising=False)
        with patch("augint_tools.dashboard._common._get_gh_cli_token", return_value=""):
            with pytest.raises(RuntimeError, match="No GitHub token"):
                _resolve_token(".env")


class TestGithubFactories:
    def test_get_github_client(self):
        with (
            patch("augint_tools.dashboard._common._resolve_token", return_value="tok"),
            patch("augint_tools.dashboard._common.Github") as mock_github,
        ):
            mock_github.return_value = "client"
            assert get_github_client() == "client"
        assert mock_github.called

    def test_get_github_repo_user_lookup(self):
        user_repo = MagicMock(name="user-repo")
        client = MagicMock()
        client.get_user.return_value.get_repo.return_value = user_repo
        with (
            patch("augint_tools.dashboard._common._resolve_token", return_value="tok"),
            patch("augint_tools.dashboard._common.Github", return_value=client),
        ):
            result = get_github_repo("owner", "repo")
        assert result is user_repo
        client.get_user.assert_called_with("owner")
        client.get_organization.assert_not_called()

    def test_get_github_repo_falls_back_to_org(self):
        org_repo = MagicMock(name="org-repo")
        client = MagicMock()
        client.get_user.return_value.get_repo.side_effect = UnknownObjectException(
            404, "nope", None
        )
        client.get_organization.return_value.get_repo.return_value = org_repo
        with (
            patch("augint_tools.dashboard._common._resolve_token", return_value="tok"),
            patch("augint_tools.dashboard._common.Github", return_value=client),
        ):
            result = get_github_repo("owner", "repo")
        assert result is org_repo
        client.get_organization.assert_called_with("owner")


class TestConfigureLogging:
    def test_verbose_without_log_file(self):
        # Smoke test: just ensure it runs without raising and installs the
        # stdlib InterceptHandler at level 0.
        configure_logging(verbose=True)
        assert logging.getLogger().level == 0

    def test_log_file_sink(self, tmp_path):
        log_file = tmp_path / "app.log"
        configure_logging(verbose=False, log_file=str(log_file))
        logger.info("hello-from-test")
        # Flush by removing sinks.
        logger.remove()
        assert log_file.exists()

    def test_chatty_stdlib_loggers_silenced(self):
        configure_logging(verbose=False)
        for name in ("github", "urllib3", "textual"):
            assert logging.getLogger(name).level == logging.WARNING
