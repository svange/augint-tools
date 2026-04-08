"""Tests for config module."""

from pathlib import Path

from augint_tools.config import load_ai_shell_config, load_workspace_config


class TestAiShellConfig:
    def test_load_library_config(self):
        config = load_ai_shell_config(Path("tests/fixtures/ai-shell-library.toml"))
        assert config is not None
        assert config.repo_type == "library"
        assert config.branch_strategy == "main"
        assert config.base_branch == "main"
        assert config.pr_target_branch == "main"

    def test_load_service_config(self):
        config = load_ai_shell_config(Path("tests/fixtures/ai-shell-service.toml"))
        assert config is not None
        assert config.repo_type == "service"
        assert config.branch_strategy == "dev"
        assert config.dev_branch == "dev"
        assert config.base_branch == "dev"
        assert config.pr_target_branch == "dev"

    def test_load_full_config(self):
        config = load_ai_shell_config(Path("tests/fixtures/ai-shell-full.toml"))
        assert config is not None
        assert config.repo_type == "library"
        assert config.ai_tools_repo.default_submit_preset == "full"
        assert config.ai_tools_repo.update_work_branch_strategy == "rebase"
        assert config.ai_tools_commands.quality == "uv run pre-commit run --all-files"
        assert config.ai_tools_commands.tests == "uv run pytest --cov=src --cov-fail-under=80 -v"
        assert config.ai_tools_commands.security == "uv run pip-audit"
        assert config.ai_tools_commands.licenses == "uv run pip-licenses --from=mixed --summary"
        assert config.ai_tools_commands.build == "uv build"

    def test_load_config_defaults_for_missing_ai_tools(self):
        """Old configs without [ai_tools] sections get defaults."""
        config = load_ai_shell_config(Path("tests/fixtures/ai-shell-library.toml"))
        assert config is not None
        assert config.ai_tools_repo.default_submit_preset == "default"
        assert config.ai_tools_repo.update_work_branch_strategy == "rebase"
        assert config.ai_tools_commands.quality is None
        assert config.ai_tools_commands.tests is None

    def test_load_nonexistent_config(self):
        config = load_ai_shell_config(Path("nonexistent.toml"))
        assert config is None


class TestWorkspaceConfig:
    def test_load_workspace_config(self):
        config = load_workspace_config(Path("tests/fixtures/workspace-example.toml"))
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
