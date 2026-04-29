"""Tests for augint_tools.config layered .env loading and git remote detection."""

from __future__ import annotations

import os
from subprocess import CompletedProcess
from unittest.mock import patch

from augint_tools.config import (
    augint_env_values,
    detect_github_remote,
    get_augint_home,
    load_augint_env,
)


class TestGetAugintHome:
    def test_default_path(self, monkeypatch):
        monkeypatch.delenv("AUGINT_HOME", raising=False)
        assert get_augint_home() == get_augint_home().home() / ".augint"

    def test_override_via_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUGINT_HOME", str(tmp_path))
        assert get_augint_home() == tmp_path


class TestAugintEnvValues:
    def test_local_only(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AUGINT_HOME", str(tmp_path / "no-global"))
        local = tmp_path / ".env"
        local.write_text("APP=local\n")
        result = augint_env_values(str(local))
        assert result == {"APP": "local"}

    def test_global_only(self, tmp_path, monkeypatch):
        home = tmp_path / "augint-home"
        home.mkdir()
        (home / ".env").write_text("SHARED_KEY=global\n")
        monkeypatch.setenv("AUGINT_HOME", str(home))
        result = augint_env_values(str(tmp_path / "nonexistent.env"))
        assert result == {"SHARED_KEY": "global"}

    def test_local_overrides_global(self, tmp_path, monkeypatch):
        home = tmp_path / "augint-home"
        home.mkdir()
        (home / ".env").write_text("TOKEN=global-tok\nSHARED=from-global\n")
        monkeypatch.setenv("AUGINT_HOME", str(home))
        local = tmp_path / ".env"
        local.write_text("TOKEN=local-tok\nLOCAL_ONLY=yes\n")
        result = augint_env_values(str(local))
        assert result["TOKEN"] == "local-tok"
        assert result["SHARED"] == "from-global"
        assert result["LOCAL_ONLY"] == "yes"

    def test_no_files_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AUGINT_HOME", str(tmp_path / "empty"))
        result = augint_env_values(str(tmp_path / "nope.env"))
        assert result == {}


class TestLoadAugintEnv:
    def test_loads_global_then_local(self, tmp_path, monkeypatch):
        home = tmp_path / "augint-home"
        home.mkdir()
        (home / ".env").write_text("GLOBAL_VAR=gval\nOVERLAP=global\n")
        monkeypatch.setenv("AUGINT_HOME", str(home))
        local = tmp_path / ".env"
        local.write_text("LOCAL_VAR=lval\nOVERLAP=local\n")

        monkeypatch.delenv("GLOBAL_VAR", raising=False)
        monkeypatch.delenv("LOCAL_VAR", raising=False)
        monkeypatch.delenv("OVERLAP", raising=False)

        load_augint_env(str(local))

        assert os.environ["GLOBAL_VAR"] == "gval"
        assert os.environ["LOCAL_VAR"] == "lval"
        assert os.environ["OVERLAP"] == "local"

    def test_file_overrides_process_env(self, tmp_path, monkeypatch):
        """Matches original load_dotenv(override=True) behaviour."""
        home = tmp_path / "augint-home"
        home.mkdir()
        (home / ".env").write_text("MY_KEY=from-file\n")
        monkeypatch.setenv("AUGINT_HOME", str(home))
        monkeypatch.setenv("MY_KEY", "from-process")

        load_augint_env(str(tmp_path / "nonexistent.env"))

        assert os.environ["MY_KEY"] == "from-file"


class TestDetectGithubRemote:
    def test_https_url(self):
        with patch("augint_tools.config.subprocess.run") as mock_run:
            mock_run.return_value = CompletedProcess(
                args=["git", "remote", "get-url", "origin"],
                returncode=0,
                stdout="https://github.com/myorg/myrepo.git\n",
            )
            assert detect_github_remote() == ("myorg", "myrepo")

    def test_ssh_url(self):
        with patch("augint_tools.config.subprocess.run") as mock_run:
            mock_run.return_value = CompletedProcess(
                args=["git", "remote", "get-url", "origin"],
                returncode=0,
                stdout="git@github.com:myorg/myrepo.git\n",
            )
            assert detect_github_remote() == ("myorg", "myrepo")

    def test_no_dotgit_suffix(self):
        with patch("augint_tools.config.subprocess.run") as mock_run:
            mock_run.return_value = CompletedProcess(
                args=["git", "remote", "get-url", "origin"],
                returncode=0,
                stdout="https://github.com/user/repo\n",
            )
            assert detect_github_remote() == ("user", "repo")

    def test_not_a_git_repo(self):
        with patch(
            "augint_tools.config.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            assert detect_github_remote() is None

    def test_non_github_remote(self):
        with patch("augint_tools.config.subprocess.run") as mock_run:
            mock_run.return_value = CompletedProcess(
                args=["git", "remote", "get-url", "origin"],
                returncode=0,
                stdout="https://gitlab.com/myorg/myrepo.git\n",
            )
            assert detect_github_remote() is None
