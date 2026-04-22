"""Tests for env auth and token resolution."""

from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from augint_tools.env.auth import load_env_config, resolve_token


class TestLoadEnvConfig:
    def test_prefers_process_env_over_dotenv(self, tmp_path, monkeypatch):
        env_path = tmp_path / ".env"
        env_path.write_text("GH_REPO=file-repo\nGH_ACCOUNT=file-account\nGH_TOKEN=file-token\n")

        monkeypatch.setenv("GH_REPO", "env-repo")
        monkeypatch.setenv("GH_ACCOUNT", "env-account")
        monkeypatch.setenv("GH_TOKEN", "env-token")

        assert load_env_config(str(env_path)) == ("env-repo", "env-account", "env-token")

    def test_reads_dotenv_when_process_env_missing(self, tmp_path, monkeypatch):
        env_path = tmp_path / ".env"
        env_path.write_text("GH_REPO=file-repo\nGH_ACCOUNT=file-account\nGH_TOKEN=file-token\n")

        monkeypatch.delenv("GH_REPO", raising=False)
        monkeypatch.delenv("GH_ACCOUNT", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)

        assert load_env_config(str(env_path)) == ("file-repo", "file-account", "file-token")


class TestResolveToken:
    def test_prefers_gh_cli_over_env_var(self, tmp_path, monkeypatch):
        env_path = tmp_path / ".env"
        env_path.write_text("GH_TOKEN=file-token\n")
        monkeypatch.setenv("GH_TOKEN", "env-token")

        with patch("augint_tools.env.auth.subprocess.run") as mock_run:
            mock_run.return_value = CompletedProcess(
                args=["gh", "auth", "token"],
                returncode=0,
                stdout="gh-token\n",
            )
            assert resolve_token(str(env_path)) == "gh-token"

    def test_gh_cli_probe_strips_env_token(self, tmp_path, monkeypatch):
        """gh auth token must run with GH_TOKEN stripped so it returns the keyring value."""
        env_path = tmp_path / ".env"
        env_path.write_text("")
        monkeypatch.setenv("GH_TOKEN", "env-token")
        monkeypatch.setenv("GITHUB_TOKEN", "env-gh-token")

        with patch("augint_tools.env.auth.subprocess.run") as mock_run:
            mock_run.return_value = CompletedProcess(
                args=["gh", "auth", "token"],
                returncode=0,
                stdout="keyring-token\n",
            )
            resolve_token(str(env_path))
            passed_env = mock_run.call_args.kwargs["env"]
            assert "GH_TOKEN" not in passed_env
            assert "GITHUB_TOKEN" not in passed_env

    def test_falls_back_to_env_var_when_gh_cli_unavailable(self, tmp_path, monkeypatch):
        env_path = tmp_path / ".env"
        env_path.write_text("GH_TOKEN=file-token\n")
        monkeypatch.setenv("GH_TOKEN", "env-token")

        with patch(
            "augint_tools.env.auth.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            assert resolve_token(str(env_path)) == "env-token"

    def test_falls_back_to_dotenv_when_no_keyring_and_no_env_var(self, tmp_path, monkeypatch):
        env_path = tmp_path / ".env"
        env_path.write_text("GH_TOKEN=file-token\n")
        monkeypatch.delenv("GH_TOKEN", raising=False)

        with patch(
            "augint_tools.env.auth.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            assert resolve_token(str(env_path)) == "file-token"

    def test_dotenv_mode_uses_dotenv_file(self, tmp_path, monkeypatch):
        env_path = tmp_path / ".env"
        env_path.write_text("GH_TOKEN=file-token\n")
        monkeypatch.setenv("GH_TOKEN", "env-token")

        with patch("augint_tools.env.auth.subprocess.run") as mock_run:
            assert resolve_token(str(env_path), auth_source="dotenv") == "file-token"
            mock_run.assert_not_called()

    def test_dotenv_mode_requires_dotenv_token(self, tmp_path, monkeypatch):
        env_path = tmp_path / ".env"
        env_path.write_text("GH_ACCOUNT=myorg\n")
        monkeypatch.delenv("GH_TOKEN", raising=False)

        with pytest.raises(RuntimeError, match="No GitHub token found in \\.env"):
            resolve_token(str(env_path), auth_source="dotenv")

    def test_raises_when_no_token_found(self, tmp_path, monkeypatch):
        env_path = tmp_path / ".env"
        env_path.write_text("APP_NAME=test\n")
        monkeypatch.delenv("GH_TOKEN", raising=False)

        with patch(
            "augint_tools.env.auth.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            with pytest.raises(RuntimeError, match="No GitHub token found"):
                resolve_token(str(env_path))

    def test_gh_cli_empty_output_falls_back(self, tmp_path, monkeypatch):
        """gh auth token exiting 0 with empty stdout (no keyring) should fall back."""
        env_path = tmp_path / ".env"
        env_path.write_text("GH_TOKEN=file-token\n")
        monkeypatch.setenv("GH_TOKEN", "env-token")

        with patch("augint_tools.env.auth.subprocess.run") as mock_run:
            mock_run.return_value = CompletedProcess(
                args=["gh", "auth", "token"],
                returncode=0,
                stdout="\n",
            )
            assert resolve_token(str(env_path)) == "env-token"
