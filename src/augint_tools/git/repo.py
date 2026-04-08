"""Basic git repository operations."""

import subprocess
from pathlib import Path


def run_git(
    args: list[str], cwd: Path | None = None, check: bool = True
) -> subprocess.CompletedProcess:
    """
    Run a git command.

    Args:
        args: Git command arguments
        cwd: Working directory (defaults to current directory)
        check: Whether to raise on non-zero exit

    Returns:
        CompletedProcess result
    """
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
    )


def is_git_repo(path: Path | None = None) -> bool:
    """
    Check if path is inside a git repository.

    Args:
        path: Path to check (defaults to current directory)

    Returns:
        True if inside a git repository
    """
    try:
        result = run_git(["rev-parse", "--git-dir"], cwd=path, check=False)
        return result.returncode == 0
    except Exception:
        return False


def get_current_branch(path: Path | None = None) -> str | None:
    """
    Get current branch name.

    Args:
        path: Repository path (defaults to current directory)

    Returns:
        Branch name or None if detached/error
    """
    try:
        result = run_git(["branch", "--show-current"], cwd=path, check=False)
        if result.returncode == 0 and result.stdout.strip():
            return str(result.stdout.strip())
        return None
    except Exception:
        return None


def get_remote_url(path: Path | None = None, remote: str = "origin") -> str | None:
    """
    Get remote URL.

    Args:
        path: Repository path (defaults to current directory)
        remote: Remote name (default: "origin")

    Returns:
        Remote URL or None if not found
    """
    try:
        result = run_git(["remote", "get-url", remote], cwd=path, check=False)
        if result.returncode == 0:
            return str(result.stdout.strip())
        return None
    except Exception:
        return None


def detect_base_branch(path: Path | None = None) -> str:
    """
    Detect the base branch for the repository.

    Checks for main, master, dev, develop in that order.

    Args:
        path: Repository path (defaults to current directory)

    Returns:
        Base branch name (defaults to "main" if none found)
    """
    candidates = ["main", "master", "dev", "develop"]

    try:
        # Get all branches
        result = run_git(["branch", "-a"], cwd=path, check=False)
        if result.returncode != 0:
            return "main"

        branches = [line.strip().lstrip("* ") for line in result.stdout.split("\n")]

        # Check for local branches first
        for candidate in candidates:
            if candidate in branches:
                return candidate

        # Check for remote branches
        for candidate in candidates:
            if f"remotes/origin/{candidate}" in branches:
                return candidate

        return "main"
    except Exception:
        return "main"
