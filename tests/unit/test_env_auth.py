"""Tests for env auth and token resolution."""

from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from augint_tools.env.auth import load_env_config, resolve_token


@pytest.fixture(autouse=True)
def _isolate_augint_home(tmp_path, monkeypatch):
    """Prevent tests from reading a real ~/.augint/.env."""
    monkeypatch.setenv("AUGINT_HOME", str(tmp_path / "no-augint-home"))


class TestLoadEnvConfig:
    def test_reads_env_vars_by_default(self, monkeypatch):
        monkeypatch.setenv("GH_REPO", "env-repo")
        monkeypatch.setenv("GH_ACCOUNT", "env-account")
        monkeypatch.setenv("GH_TOKEN", "env-token")

        assert load_env_config() == ("env-repo", "env-account", "env-token")

    @patch("augint_tools.env.auth.detect_github_remote", return_value=None)
    def test_no_env_file_ignores_dotenv(self, _mock_remote, tmp_path, monkeypatch):
        env_path = tmp_path / ".env"
        env_path.write_text("GH_REPO=file-repo\nGH_ACCOUNT=file-account\nGH_TOKEN=file-token\n")

        monkeypatch.delenv("GH_REPO", raising=False)
        monkeypatch.delenv("GH_ACCOUNT", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)

        assert load_env_config() == ("", "", "")

    def test_env_file_reads_dotenv(self, tmp_path, monkeypatch):
        env_path = tmp_path / ".env"
        env_path.write_text("GH_REPO=file-repo\nGH_ACCOUNT=file-account\nGH_TOKEN=file-token\n")

        monkeypatch.delenv("GH_REPO", raising=False)
        monkeypatch.delenv("GH_ACCOUNT", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)

        assert load_env_config(env_file=str(env_path)) == (
            "file-repo",
            "file-account",
            "file-token",
        )

    def test_env_file_overrides_process_env(self, tmp_path, monkeypatch):
        env_path = tmp_path / ".env"
        env_path.write_text("GH_REPO=file-repo\nGH_ACCOUNT=file-account\n")

        monkeypatch.setenv("GH_REPO", "env-repo")
        monkeypatch.setenv("GH_ACCOUNT", "env-account")

        repo, account, _ = load_env_config(env_file=str(env_path))
        assert repo == "file-repo"
        assert account == "file-account"

    @patch("augint_tools.env.auth.detect_github_remote", return_value=("remote-org", "remote-repo"))
    def test_git_remote_fallback(self, _mock_remote, monkeypatch):
        monkeypatch.delenv("GH_REPO", raising=False)
        monkeypatch.delenv("GH_ACCOUNT", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)

        repo, account, _ = load_env_config()
        assert repo == "remote-repo"
        assert account == "remote-org"

    @patch("augint_tools.env.auth.detect_github_remote", return_value=("remote-org", "remote-repo"))
    def test_env_vars_override_git_remote(self, _mock_remote, monkeypatch):
        monkeypatch.setenv("GH_REPO", "env-repo")
        monkeypatch.setenv("GH_ACCOUNT", "env-account")

        repo, account, _ = load_env_config()
        assert repo == "env-repo"
        assert account == "env-account"

    @patch("augint_tools.env.auth.detect_github_remote", return_value=("remote-org", "remote-repo"))
    def test_partial_env_var_uses_remote_for_missing(self, _mock_remote, monkeypatch):
        monkeypatch.setenv("GH_ACCOUNT", "env-account")
        monkeypatch.delenv("GH_REPO", raising=False)

        repo, account, _ = load_env_config()
        assert repo == "remote-repo"
        assert account == "env-account"


class TestResolveToken:
    def test_prefers_gh_cli(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "env-token")

        with patch("augint_tools.env.auth.subprocess.run") as mock_run:
            mock_run.return_value = CompletedProcess(
                args=["gh", "auth", "token"],
                returncode=0,
                stdout="gh-token\n",
            )
            assert resolve_token() == "gh-token"

    def test_gh_cli_probe_strips_env_token(self, monkeypatch):
        """gh auth token must run with GH_TOKEN stripped so it returns the keyring value."""
        monkeypatch.setenv("GH_TOKEN", "env-token")
        monkeypatch.setenv("GITHUB_TOKEN", "env-gh-token")

        with patch("augint_tools.env.auth.subprocess.run") as mock_run:
            mock_run.return_value = CompletedProcess(
                args=["gh", "auth", "token"],
                returncode=0,
                stdout="keyring-token\n",
            )
            resolve_token()
            passed_env = mock_run.call_args.kwargs["env"]
            assert "GH_TOKEN" not in passed_env
            assert "GITHUB_TOKEN" not in passed_env

    def test_falls_back_to_env_var(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "env-token")

        with patch(
            "augint_tools.env.auth.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            assert resolve_token() == "env-token"

    def test_raises_when_no_token_found(self, monkeypatch):
        monkeypatch.delenv("GH_TOKEN", raising=False)

        with patch(
            "augint_tools.env.auth.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            with pytest.raises(RuntimeError, match="No GitHub token found"):
                resolve_token()

    def test_env_file_reads_from_dotenv(self, tmp_path, monkeypatch):
        env_path = tmp_path / ".env"
        env_path.write_text("GH_TOKEN=file-token\n")
        monkeypatch.delenv("GH_TOKEN", raising=False)

        assert resolve_token(env_file=str(env_path)) == "file-token"

    def test_env_file_raises_when_missing_token(self, tmp_path, monkeypatch):
        env_path = tmp_path / ".env"
        env_path.write_text("GH_ACCOUNT=myorg\n")
        monkeypatch.delenv("GH_TOKEN", raising=False)

        with pytest.raises(RuntimeError, match="No GH_TOKEN found in .env"):
            resolve_token(env_file=str(env_path))

    def test_gh_cli_empty_output_falls_back_to_env(self, monkeypatch):
        """gh auth token exiting 0 with empty stdout should fall back to env var."""
        monkeypatch.setenv("GH_TOKEN", "env-token")

        with patch("augint_tools.env.auth.subprocess.run") as mock_run:
            mock_run.return_value = CompletedProcess(
                args=["gh", "auth", "token"],
                returncode=0,
                stdout="\n",
            )
            assert resolve_token() == "env-token"

    def test_default_does_not_read_dotenv(self, tmp_path, monkeypatch):
        """Without --env, a .env file with GH_TOKEN should be ignored."""
        env_path = tmp_path / ".env"
        env_path.write_text("GH_TOKEN=file-token\n")
        monkeypatch.delenv("GH_TOKEN", raising=False)

        with patch(
            "augint_tools.env.auth.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            with pytest.raises(RuntimeError, match="No GitHub token found"):
                resolve_token()
