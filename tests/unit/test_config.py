"""Tests for config module."""

from pathlib import Path

from augint_tools.config import load_workspace_config


class TestWorkspaceConfig:
    def test_load_workspace_config(self):
        config = load_workspace_config(Path("tests/fixtures/workspace-example.yaml"))
        assert config is not None
        assert config.name == "test-workspace"
        assert config.repos_dir == "repos"
        assert len(config.repos) == 2

        lib_a = config.repos[0]
        assert lib_a.name == "lib-a"
        assert lib_a.repo_type == "library"
        assert lib_a.depends_on == []

        lib_b = config.repos[1]
        assert lib_b.name == "lib-b"
        assert lib_b.depends_on == ["lib-a"]

    def test_load_nonexistent_workspace(self):
        config = load_workspace_config(Path("nonexistent.toml"))
        assert config is None
