"""ai-shell.toml configuration parsing."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

import tomli_w


@dataclass
class AiShellConfig:
    """Configuration from ai-shell.toml."""

    repo_type: str  # "library", "service", or "workspace"
    branch_strategy: str  # "main" or "dev"
    dev_branch: str = "dev"  # Name of dev branch (only used when strategy is "dev")

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
    """
    Load ai-shell.toml configuration.

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

        # Map old "iac" to "service" for backward compatibility
        repo_type = project.get("repo_type", "library")
        if repo_type == "iac":
            repo_type = "service"

        return AiShellConfig(
            repo_type=repo_type,
            branch_strategy=project.get("branch_strategy", "main"),
            dev_branch=project.get("dev_branch", "dev"),
        )
    except Exception:
        # Return None for any parsing errors
        return None


def create_ai_shell_config(
    path: Path,
    repo_type: str,
    branch_strategy: str | None = None,
    dev_branch: str = "dev",
) -> None:
    """
    Create or update ai-shell.toml configuration.

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
    existing_data = {}
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
        # Remove dev_branch if switching to main strategy
        del existing_data["project"]["dev_branch"]

    # Write config
    with open(path, "wb") as f:
        tomli_w.dump(existing_data, f)
