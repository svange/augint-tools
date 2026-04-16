"""Tests for env sync functionality."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from augint_tools.env.sync import _sync_secrets, _sync_variables


class TestSyncSecrets:
    def test_creates_new_secrets(self):
        repo = MagicMock()
        repo.get_secrets.return_value = []
        repo.create_secret.return_value = True

        result = asyncio.run(_sync_secrets(repo, {"MY_SECRET": "val"}, dry_run=False))
        assert result == ["MY_SECRET"]
        repo.create_secret.assert_called_once_with("MY_SECRET", "val")

    def test_dry_run_skips_api_calls(self):
        repo = MagicMock()
        existing = MagicMock()
        existing.name = "OLD_SECRET"
        repo.get_secrets.return_value = [existing]

        result = asyncio.run(_sync_secrets(repo, {"NEW_SECRET": "val"}, dry_run=True))
        assert result == ["NEW_SECRET"]
        repo.create_secret.assert_not_called()
        repo.delete_secret.assert_not_called()


class TestSyncVariables:
    def test_creates_new_variables(self):
        repo = MagicMock()
        repo.get_variables.return_value = []
        repo.create_variable.return_value = True

        result = asyncio.run(_sync_variables(repo, {"APP_NAME": "myapp"}, dry_run=False))
        assert result == ["APP_NAME"]
        repo.create_variable.assert_called_once_with("APP_NAME", "myapp")

    def test_updates_existing_via_delete_create(self):
        repo = MagicMock()
        existing = MagicMock()
        existing.name = "APP_NAME"
        repo.get_variables.return_value = [existing]

        result = asyncio.run(_sync_variables(repo, {"APP_NAME": "newval"}, dry_run=False))
        assert result == ["APP_NAME"]
        repo.delete_variable.assert_called_once_with("APP_NAME")

    def test_dry_run_skips_api_calls(self):
        repo = MagicMock()
        repo.get_variables.return_value = []

        result = asyncio.run(_sync_variables(repo, {"APP_NAME": "myapp"}, dry_run=True))
        assert result == ["APP_NAME"]
        repo.create_variable.assert_not_called()


class TestPerformSync:
    @patch("augint_tools.env.sync.get_github_repo")
    @patch("augint_tools.env.sync.partition_env")
    @patch("augint_tools.env.sync.load_dotenv")
    def test_perform_sync_calls_partition(
        self, mock_dotenv, mock_partition, mock_repo, monkeypatch
    ):
        monkeypatch.setenv("GH_REPO", "test-repo")
        monkeypatch.setenv("GH_ACCOUNT", "test-account")

        mock_partition.return_value = ({"SECRET_KEY": "val"}, {"APP_NAME": "myapp"})

        repo = MagicMock()
        repo.get_secrets.return_value = []
        repo.get_variables.return_value = []
        repo.create_secret.return_value = True
        repo.create_variable.return_value = True
        mock_repo.return_value = repo

        from augint_tools.env.sync import perform_sync

        result = asyncio.run(perform_sync(".env", dry_run=False))
        assert "SECRET_KEY" in result["secrets"]
        assert "APP_NAME" in result["variables"]

    @patch("augint_tools.env.sync.load_dotenv")
    def test_missing_gh_repo_raises(self, mock_dotenv, monkeypatch):
        monkeypatch.delenv("GH_REPO", raising=False)
        monkeypatch.delenv("GH_ACCOUNT", raising=False)

        from augint_tools.env.sync import perform_sync

        with pytest.raises(RuntimeError, match="GH_REPO and GH_ACCOUNT"):
            asyncio.run(perform_sync(".env"))
