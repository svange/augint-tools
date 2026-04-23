"""Main detection engine: one-call snapshot of repo environment."""

from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from augint_tools.detection.commands import CommandPlan, resolve_command_plan
from augint_tools.detection.framework import detect_framework
from augint_tools.detection.language import detect_language
from augint_tools.detection.toolchain import ToolchainInfo, detect_toolchain
from augint_tools.git.repo import (
    detect_base_branch,
    extract_repo_slug,
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

    language: str  # python, typescript, mixed, unknown
    framework: str  # plain, sam, cdk, terraform, vite, nextjs
    default_branch: str
    current_branch: str | None
    target_pr_branch: str
    toolchain: ToolchainInfo
    command_plan: CommandPlan
    github: GitHubState
    path: Path

    def to_dict(self) -> dict:
        """Serialize for JSON output."""
        return {
            "language": self.language,
            "framework": self.framework,
            "default_branch": self.default_branch,
            "current_branch": self.current_branch,
            "target_pr_branch": self.target_pr_branch,
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
            "path": str(self.path),
        }


def detect(path: Path | None = None) -> RepoContext:
    """One-call detection: probes filesystem, checks git and GitHub.

    Args:
        path: Repository root path. Defaults to cwd.

    Returns:
        Frozen snapshot of the repo environment.
    """
    if path is None:
        path = Path.cwd()

    # Language and framework
    language = detect_language(path)
    framework = detect_framework(path)

    # Toolchain
    toolchain = detect_toolchain(path)

    # Command plan from ecosystem defaults, framework-aware
    command_plan = resolve_command_plan(toolchain, language, framework)

    # Git state
    default_branch = "main"
    current_branch = None
    if is_git_repo(path):
        default_branch = detect_base_branch(path)
        current_branch = get_current_branch(path)

    # GitHub state
    github = GitHubState()
    if is_gh_available():
        github.available = True
        if is_gh_authenticated():
            github.authenticated = True
            remote_url = get_remote_url(path) if is_git_repo(path) else None
            if remote_url:
                github.repo_slug = extract_repo_slug(remote_url)

    ctx = RepoContext(
        language=language,
        framework=framework,
        default_branch=default_branch,
        current_branch=current_branch,
        target_pr_branch=default_branch,
        toolchain=toolchain,
        command_plan=command_plan,
        github=github,
        path=path,
    )
    logger.debug(
        "detect: lang={} framework={} branch={} toolchain={}",
        language,
        framework,
        current_branch,
        toolchain.package_manager,
    )
    return ctx
