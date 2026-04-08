"""Tests for config module."""

from pathlib import Path

from augint_tools.config import load_ai_shell_config, load_workspace_config


class TestAiShellConfig:
    def test_load_library_config(self):
        """Test loading library config."""
        config_path = Path("tests/fixtures/ai-shell-library.toml")
        config = load_ai_shell_config(config_path)

        assert config is not None
        assert config.repo_type == "library"
        assert config.branch_strategy == "main"
        assert config.base_branch == "main"
        assert config.pr_target_branch == "main"

    def test_load_service_config(self):
        """Test loading service config."""
        config_path = Path("tests/fixtures/ai-shell-service.toml")
        config = load_ai_shell_config(config_path)

        assert config is not None
        assert config.repo_type == "service"
        assert config.branch_strategy == "dev"
        assert config.dev_branch == "dev"
        assert config.base_branch == "dev"
        assert config.pr_target_branch == "dev"

    def test_load_nonexistent_config(self):
        """Test loading nonexistent config."""
        config = load_ai_shell_config(Path("nonexistent.toml"))
        assert config is None


class TestWorkspaceConfig:
    def test_load_workspace_config(self):
        """Test loading workspace config."""
        config_path = Path("tests/fixtures/workspace-example.toml")
        config = load_workspace_config(config_path)

        assert config is not None
        assert config.name == "test-workspace"
        assert config.repos_dir == "repos"
        assert len(config.repos) == 2

        # Check first repo
        lib_a = config.repos[0]
        assert lib_a.name == "lib-a"
        assert lib_a.path == "repos/lib-a"
        assert lib_a.repo_type == "library"
        assert lib_a.base_branch == "main"
        assert lib_a.test == "pytest -v"
        assert lib_a.depends_on == []

        # Check second repo with dependency
        lib_b = config.repos[1]
        assert lib_b.name == "lib-b"
        assert lib_b.depends_on == ["lib-a"]

    def test_load_nonexistent_workspace(self):
        """Test loading nonexistent workspace config."""
        config = load_workspace_config(Path("nonexistent.toml"))
        assert config is None
