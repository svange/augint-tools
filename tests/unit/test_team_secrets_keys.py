"""Tests for team_secrets.keys module."""

import stat
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from augint_tools.team_secrets.keys import (
    bootstrap_key,
    get_cached_key,
    get_config_dir,
    get_key_cache_path,
    load_teams_config,
    require_key,
    resolve_github_username,
    resolve_repo_path,
    save_team_config,
    verify_key_permissions,
)
from augint_tools.team_secrets.models import TeamConfig


def test_get_config_dir():
    result = get_config_dir()
    assert result == Path.home() / ".augint-tools"


def test_get_key_cache_path():
    result = get_key_cache_path("woxom")
    assert result == Path.home() / ".augint-tools" / "keys" / "woxom" / "age-key.txt"


def test_load_teams_config_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "augint_tools.team_secrets.keys.get_teams_config_path",
        lambda: tmp_path / "teams.yaml",
    )
    assert load_teams_config() == {}


def test_load_teams_config_with_data(tmp_path, monkeypatch):
    config_file = tmp_path / "teams.yaml"
    config_file.write_text("woxom:\n  repo_path: /home/user/woxom-secrets\n  username: sam\n")
    monkeypatch.setattr(
        "augint_tools.team_secrets.keys.get_teams_config_path",
        lambda: config_file,
    )
    configs = load_teams_config()
    assert "woxom" in configs
    assert configs["woxom"].username == "sam"
    assert configs["woxom"].repo_path == Path("/home/user/woxom-secrets")


def test_save_team_config(tmp_path, monkeypatch):
    config_file = tmp_path / "teams.yaml"
    monkeypatch.setattr(
        "augint_tools.team_secrets.keys.get_teams_config_path",
        lambda: config_file,
    )
    config = TeamConfig(name="woxom", repo_path=Path("/tmp/secrets"), username="sam")
    save_team_config(config)

    assert config_file.exists()
    content = config_file.read_text()
    assert "woxom" in content
    assert "sam" in content


def test_resolve_repo_path_flag():
    result = resolve_repo_path("woxom", "/tmp/my-repo")
    assert result == Path("/tmp/my-repo")


def test_resolve_repo_path_config(tmp_path, monkeypatch):
    config_file = tmp_path / "teams.yaml"
    repo_dir = tmp_path / "woxom-secrets"
    repo_dir.mkdir()
    config_file.write_text(f"woxom:\n  repo_path: {repo_dir}\n  username: sam\n")
    monkeypatch.setattr(
        "augint_tools.team_secrets.keys.get_teams_config_path",
        lambda: config_file,
    )
    result = resolve_repo_path("woxom")
    assert result == repo_dir


def test_resolve_repo_path_sibling(tmp_path, monkeypatch):
    sibling = tmp_path / "woxom-secrets"
    sibling.mkdir()
    work_dir = tmp_path / "project"
    work_dir.mkdir()
    monkeypatch.chdir(work_dir)
    monkeypatch.setattr(
        "augint_tools.team_secrets.keys.get_teams_config_path",
        lambda: tmp_path / "nonexistent.yaml",
    )
    result = resolve_repo_path("woxom")
    assert result == sibling


def test_resolve_repo_path_not_found(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "augint_tools.team_secrets.keys.get_teams_config_path",
        lambda: tmp_path / "nonexistent.yaml",
    )
    result = resolve_repo_path("nonexistent")
    assert result is None


def test_resolve_github_username():
    with patch("augint_tools.team_secrets.keys.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="svange\n")
        assert resolve_github_username() == "svange"


def test_resolve_github_username_not_available():
    with patch("augint_tools.team_secrets.keys.subprocess.run") as mock_run:
        mock_run.side_effect = FileNotFoundError()
        assert resolve_github_username() is None


def test_verify_key_permissions(tmp_path):
    key_file = tmp_path / "key.txt"
    key_file.write_text("secret")
    key_file.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 600
    if sys.platform != "win32":
        assert verify_key_permissions(key_file) is True


def test_verify_key_permissions_bad(tmp_path):
    key_file = tmp_path / "key.txt"
    key_file.write_text("secret")
    key_file.chmod(0o644)  # Too open
    if sys.platform != "win32":
        assert verify_key_permissions(key_file) is False


def test_get_cached_key_exists(tmp_path, monkeypatch):
    key_file = tmp_path / "keys" / "woxom" / "age-key.txt"
    key_file.parent.mkdir(parents=True)
    key_file.write_text("AGE-SECRET-KEY-1TEST")
    monkeypatch.setattr(
        "augint_tools.team_secrets.keys.get_key_cache_path",
        lambda team: key_file,
    )
    assert get_cached_key("woxom") == key_file


def test_get_cached_key_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "augint_tools.team_secrets.keys.get_key_cache_path",
        lambda team: tmp_path / "nonexistent.txt",
    )
    assert get_cached_key("woxom") is None


def test_require_key_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "augint_tools.team_secrets.keys.get_cached_key",
        lambda team: None,
    )
    with pytest.raises(Exception) as exc_info:
        require_key("woxom")
    assert "setup" in str(exc_info.value)


def test_bootstrap_key(tmp_path, monkeypatch):
    # Set up encrypted key file
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    keys_dir = repo_path / "keys"
    keys_dir.mkdir()
    encrypted_file = keys_dir / "sam.key.enc"
    encrypted_file.write_bytes(b"encrypted content")

    cache_path = tmp_path / "cache" / "age-key.txt"
    monkeypatch.setattr(
        "augint_tools.team_secrets.keys.get_key_cache_path",
        lambda team: cache_path,
    )

    with patch("augint_tools.team_secrets.keys.decrypt_file_with_password") as mock_decrypt:
        mock_decrypt.return_value = "AGE-SECRET-KEY-1DECRYPTED"
        result = bootstrap_key("woxom", repo_path, "sam", "password123")

    assert result == cache_path
    assert cache_path.read_text() == "AGE-SECRET-KEY-1DECRYPTED"


def test_bootstrap_key_missing_file(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "keys").mkdir()

    with pytest.raises(FileNotFoundError):
        bootstrap_key("woxom", repo_path, "nouser", "pass")
