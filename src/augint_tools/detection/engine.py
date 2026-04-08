"""Main detection engine: one-call snapshot of repo environment."""

from dataclasses import dataclass
from pathlib import Path

from augint_tools.config.ai_shell import AiToolsCommandsConfig, load_ai_shell_config
from augint_tools.detection.commands import CommandPlan, resolve_command_plan
from augint_tools.detection.framework import detect_framework
from augint_tools.detection.language import detect_language
from augint_tools.detection.toolchain import ToolchainInfo, detect_toolchain
from augint_tools.git.repo import (
    detect_base_branch,
    get_current_branch,
    get_remote_url,
    is_git_repo,
)
from augint_tools.github.cli import is_gh_authenticated, is_gh_available


@dataclass
class GitHubState:
    """GitHub CLI availability and repo info."""

    available: bool = False
    authenticated: bool = False
    repo_slug: str | None = None  # "owner/repo"


@dataclass
class RepoContext:
    """One-call snapshot of the repo environment.

    Every command should call detect() once and pass this down.
    """

    repo_kind: str  # library, service, workspace
    language: str  # python, typescript, mixed, unknown
    framework: str  # plain, sam, cdk, terraform, vite, nextjs
    default_branch: str
    dev_branch: str | None
    current_branch: str | None
    target_pr_branch: str
    branch_strategy: str  # main, dev
    toolchain: ToolchainInfo
    command_plan: CommandPlan
    github: GitHubState
    config_source: str  # ai-shell.toml, detected, default
    path: Path

    def to_dict(self) -> dict:
        """Serialize for JSON output."""
        return {
            "repo_kind": self.repo_kind,
            "language": self.language,
            "framework": self.framework,
            "default_branch": self.default_branch,
            "dev_branch": self.dev_branch,
            "current_branch": self.current_branch,
            "target_pr_branch": self.target_pr_branch,
            "branch_strategy": self.branch_strategy,
            "toolchain": {
                "package_manager": self.toolchain.package_manager,
                "has_pre_commit": self.toolchain.has_pre_commit,
                "has_pytest": self.toolchain.has_pytest,
                "has_ruff": self.toolchain.has_ruff,
                "has_mypy": self.toolchain.has_mypy,
            },
            "command_plan": {
                "quality": self.command_plan.quality,
                "tests": self.command_plan.tests,
                "security": self.command_plan.security,
                "licenses": self.command_plan.licenses,
                "build": self.command_plan.build,
            },
            "github": {
                "available": self.github.available,
                "authenticated": self.github.authenticated,
                "repo_slug": self.github.repo_slug,
            },
            "config_source": self.config_source,
            "path": str(self.path),
        }


def detect(path: Path | None = None) -> RepoContext:
    """One-call detection: reads config, probes filesystem, checks git and GitHub.

    Args:
        path: Repository root path. Defaults to cwd.

    Returns:
        Frozen snapshot of the repo environment.
    """
    if path is None:
        path = Path.cwd()

    # Load config
    config = load_ai_shell_config(path / "ai-shell.toml")
    config_source = "ai-shell.toml" if config else "detected"

    # Repo kind
    if config:
        repo_kind = config.repo_type
        branch_strategy = config.branch_strategy
        dev_branch = config.dev_branch if branch_strategy == "dev" else None
    else:
        repo_kind = "library"  # safe default
        branch_strategy = "main"
        dev_branch = None
        config_source = "default"

    # Language and framework
    language = detect_language(path)
    framework = detect_framework(path)

    # Toolchain
    toolchain = detect_toolchain(path)

    # Command plan
    config_commands: AiToolsCommandsConfig | None = config.ai_tools_commands if config else None
    command_plan = resolve_command_plan(config_commands, toolchain, language)

    # Git state
    default_branch = "main"
    current_branch = None
    if is_git_repo(path):
        default_branch = detect_base_branch(path)
        current_branch = get_current_branch(path)

    # Target branch for PRs
    if branch_strategy == "dev" and dev_branch:
        target_pr_branch = dev_branch
    else:
        target_pr_branch = default_branch

    # GitHub state
    github = GitHubState()
    if is_gh_available():
        github.available = True
        if is_gh_authenticated():
            github.authenticated = True
            # Extract repo slug from remote URL
            remote_url = get_remote_url(path) if is_git_repo(path) else None
            if remote_url:
                github.repo_slug = _extract_repo_slug(remote_url)

    return RepoContext(
        repo_kind=repo_kind,
        language=language,
        framework=framework,
        default_branch=default_branch,
        dev_branch=dev_branch,
        current_branch=current_branch,
        target_pr_branch=target_pr_branch,
        branch_strategy=branch_strategy,
        toolchain=toolchain,
        command_plan=command_plan,
        github=github,
        config_source=config_source,
        path=path,
    )


def _extract_repo_slug(remote_url: str) -> str | None:
    """Extract owner/repo from a git remote URL.

    Handles both HTTPS and SSH formats:
        https://github.com/owner/repo.git -> owner/repo
        git@github.com:owner/repo.git -> owner/repo
    """
    url = remote_url.rstrip("/").removesuffix(".git")

    if "github.com" not in url:
        return None

    if url.startswith("git@"):
        # git@github.com:owner/repo
        parts = url.split(":")
        if len(parts) == 2:
            return parts[1]
    else:
        # https://github.com/owner/repo
        parts = url.split("/")
        if len(parts) >= 2:
            return f"{parts[-2]}/{parts[-1]}"

    return None
