"""Tests for team_secrets.repo module."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from augint_tools.team_secrets.repo import (
    get_encrypted_env_path,
    init_project,
    init_repo,
    is_team_repo,
    list_projects,
)


def test_init_repo(tmp_path):
    repo_path = tmp_path / "test-secrets"
    with patch("augint_tools.team_secrets.repo.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        init_repo(repo_path, "test")

    assert repo_path.exists()
    assert (repo_path / ".sops.yaml").exists()
    assert (repo_path / ".gitignore").exists()
    assert (repo_path / "README.md").exists()
    assert (repo_path / "recipients").is_dir()
    assert (repo_path / "recipients" / "team-test.txt").exists()
    assert (repo_path / "keys").is_dir()
    assert (repo_path / "keys" / "README.md").exists()
    assert (repo_path / "projects").is_dir()
    assert (repo_path / "scripts").is_dir()


def test_init_repo_idempotent(tmp_path):
    """Running init_repo twice should not overwrite existing files."""
    repo_path = tmp_path / "test-secrets"
    with patch("augint_tools.team_secrets.repo.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        init_repo(repo_path, "test")

    # Modify README
    readme = repo_path / "README.md"
    readme.write_text("custom content")

    with patch("augint_tools.team_secrets.repo.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        init_repo(repo_path, "test")

    # Should not have been overwritten
    assert readme.read_text() == "custom content"


def test_init_project(tmp_path):
    repo_path = tmp_path / "secrets"
    (repo_path / "projects").mkdir(parents=True)
    init_project(repo_path, "myapp", "woxom")

    project_dir = repo_path / "projects" / "myapp"
    assert project_dir.exists()
    assert (project_dir / "dev.enc.env").exists()
    assert (project_dir / "prod.enc.env").exists()
    assert (project_dir / "schema.yaml").exists()
    assert (project_dir / "metadata.yaml").exists()


def test_is_team_repo_valid(tmp_path):
    (tmp_path / ".sops.yaml").write_text("creation_rules: []")
    (tmp_path / "recipients").mkdir()
    (tmp_path / "projects").mkdir()
    assert is_team_repo(tmp_path) is True


def test_is_team_repo_invalid(tmp_path):
    assert is_team_repo(tmp_path) is False


def test_list_projects(tmp_path):
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    (projects_dir / "alpha").mkdir()
    (projects_dir / "beta").mkdir()
    (projects_dir / ".hidden").mkdir()
    result = list_projects(tmp_path)
    assert result == ["alpha", "beta"]


def test_list_projects_empty(tmp_path):
    assert list_projects(tmp_path) == []


def test_get_encrypted_env_path():
    result = get_encrypted_env_path(Path("/repo"), "myapp", "prod")
    assert result == Path("/repo/projects/myapp/prod.enc.env")


def test_get_encrypted_env_path_default():
    result = get_encrypted_env_path(Path("/repo"), "myapp")
    assert result == Path("/repo/projects/myapp/dev.enc.env")
