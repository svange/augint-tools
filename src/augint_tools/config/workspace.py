"""workspace.toml configuration parsing."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RepoConfig:
    """Configuration for a child repository in a workspace."""

    name: str
    path: str
    url: str
    repo_type: str
    base_branch: str
    pr_target_branch: str
    install: str = ""
    test: str = ""
    lint: str = ""
    depends_on: list[str] = field(default_factory=list)


@dataclass
class WorkspaceConfig:
    """Configuration from workspace.toml."""

    name: str
    repos_dir: str
    repos: list[RepoConfig]


def load_workspace_config(path: Path | None = None) -> WorkspaceConfig | None:
    """
    Load workspace.toml configuration.

    Args:
        path: Path to workspace.toml (defaults to current directory)

    Returns:
        WorkspaceConfig if file exists and valid, None otherwise
    """
    if path is None:
        path = Path("workspace.toml")

    if not path.exists():
        return None

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)

        workspace = data.get("workspace", {})
        repos_data = data.get("repo", [])

        repos = []
        for repo_data in repos_data:
            # Map old "iac" to "service" for backward compatibility
            repo_type = repo_data.get("repo_type", "library")
            if repo_type == "iac":
                repo_type = "service"

            repos.append(
                RepoConfig(
                    name=repo_data["name"],
                    path=repo_data["path"],
                    url=repo_data["url"],
                    repo_type=repo_type,
                    base_branch=repo_data.get("base_branch", "main"),
                    pr_target_branch=repo_data.get("pr_target_branch", "main"),
                    install=repo_data.get("install", ""),
                    test=repo_data.get("test", ""),
                    lint=repo_data.get("lint", ""),
                    depends_on=repo_data.get("depends_on", []),
                )
            )

        return WorkspaceConfig(
            name=workspace.get("name", "workspace"),
            repos_dir=workspace.get("repos_dir", "repos"),
            repos=repos,
        )
    except Exception:
        # Return None for any parsing errors
        return None
