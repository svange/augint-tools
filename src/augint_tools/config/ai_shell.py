"""ai-shell.toml configuration parsing."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import tomli_w


@dataclass
class AiToolsRepoConfig:
    """Configuration from [ai_tools.repo] section."""

    update_work_branch_strategy: str = "rebase"  # rebase | merge
    default_submit_preset: str = "default"  # quick | default | full | ci


@dataclass
class AiToolsCommandsConfig:
    """Configuration from [ai_tools.commands] section."""

    quality: str | None = None
    tests: str | None = None
    security: str | None = None
    licenses: str | None = None
    build: str | None = None


@dataclass
class AiShellConfig:
    """Configuration from ai-shell.toml."""

    repo_type: str  # "library", "service", or "workspace"
    branch_strategy: str  # "main" or "dev"
    dev_branch: str = "dev"
    ai_tools_repo: AiToolsRepoConfig = field(default_factory=AiToolsRepoConfig)
    ai_tools_commands: AiToolsCommandsConfig = field(default_factory=AiToolsCommandsConfig)

    @property
    def base_branch(self) -> str:
        """Get the base branch for feature branches."""
        if self.branch_strategy == "dev":
            return self.dev_branch
        return "main"

    @property
    def pr_target_branch(self) -> str:
        """Get the PR target branch."""
        return self.base_branch


def load_ai_shell_config(path: Path | None = None) -> AiShellConfig | None:
    """Load ai-shell.toml configuration.

    Args:
        path: Path to ai-shell.toml (defaults to current directory)

    Returns:
        AiShellConfig if file exists and valid, None otherwise
    """
    if path is None:
        path = Path("ai-shell.toml")

    if not path.exists():
        return None

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)

        project = data.get("project", {})

        repo_type = project.get("repo_type", "library")

        # Parse [ai_tools] sections
        ai_tools = data.get("ai_tools", {})
        repo_section = ai_tools.get("repo", {})
        commands_section = ai_tools.get("commands", {})

        ai_tools_repo = AiToolsRepoConfig(
            update_work_branch_strategy=repo_section.get("update_work_branch_strategy", "rebase"),
            default_submit_preset=repo_section.get("default_submit_preset", "default"),
        )

        ai_tools_commands = AiToolsCommandsConfig(
            quality=commands_section.get("quality"),
            tests=commands_section.get("tests"),
            security=commands_section.get("security"),
            licenses=commands_section.get("licenses"),
            build=commands_section.get("build"),
        )

        return AiShellConfig(
            repo_type=repo_type,
            branch_strategy=project.get("branch_strategy", "main"),
            dev_branch=project.get("dev_branch", "dev"),
            ai_tools_repo=ai_tools_repo,
            ai_tools_commands=ai_tools_commands,
        )
    except Exception:
        return None


def create_ai_shell_config(
    path: Path,
    repo_type: str,
    branch_strategy: str | None = None,
    dev_branch: str = "dev",
) -> None:
    """Create or update ai-shell.toml configuration.

    Args:
        path: Path to ai-shell.toml
        repo_type: Repository type ("library", "service", "workspace")
        branch_strategy: Branch strategy ("main" or "dev"), auto-detected if None
        dev_branch: Name of dev branch (default: "dev")
    """
    # Auto-detect branch strategy if not provided
    if branch_strategy is None:
        if repo_type == "library":
            branch_strategy = "main"
        else:
            branch_strategy = "dev"

    # Load existing config if present
    existing_data: dict = {}
    if path.exists():
        try:
            with open(path, "rb") as f:
                existing_data = tomllib.load(f)
        except Exception:
            pass

    # Update project section
    if "project" not in existing_data:
        existing_data["project"] = {}

    existing_data["project"]["repo_type"] = repo_type
    existing_data["project"]["branch_strategy"] = branch_strategy

    # Only add dev_branch if strategy is "dev"
    if branch_strategy == "dev":
        existing_data["project"]["dev_branch"] = dev_branch
    elif "dev_branch" in existing_data["project"]:
        del existing_data["project"]["dev_branch"]

    # Write config
    with open(path, "wb") as f:
        tomli_w.dump(existing_data, f)
