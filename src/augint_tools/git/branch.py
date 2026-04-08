"""Git branch operations."""

from pathlib import Path

from augint_tools.git.repo import run_git


def create_branch(path: Path | None = None, name: str = "", base: str = "main") -> bool:
    """
    Create a new branch from base branch.

    Args:
        path: Repository path (defaults to current directory)
        name: Branch name
        base: Base branch to branch from

    Returns:
        True if successful
    """
    try:
        # Create branch from base
        result = run_git(["checkout", "-b", name, base], cwd=path, check=False)
        return result.returncode == 0
    except Exception:
        return False


def switch_branch(path: Path | None = None, name: str = "") -> bool:
    """
    Switch to existing branch.

    Args:
        path: Repository path (defaults to current directory)
        name: Branch name

    Returns:
        True if successful
    """
    try:
        result = run_git(["checkout", name], cwd=path, check=False)
        return result.returncode == 0
    except Exception:
        return False


def push_branch(path: Path | None = None, branch: str = "", set_upstream: bool = True) -> bool:
    """
    Push branch to remote.

    Args:
        path: Repository path (defaults to current directory)
        branch: Branch name (uses current if empty)
        set_upstream: Whether to set upstream tracking

    Returns:
        True if successful
    """
    try:
        args = ["push"]
        if set_upstream:
            args.extend(["-u", "origin", branch])
        else:
            args.append("origin")
            if branch:
                args.append(branch)

        result = run_git(args, cwd=path, check=False)
        return result.returncode == 0
    except Exception:
        return False


def branch_exists(path: Path | None = None, name: str = "") -> bool:
    """
    Check if branch exists locally.

    Args:
        path: Repository path (defaults to current directory)
        name: Branch name

    Returns:
        True if branch exists
    """
    try:
        result = run_git(["rev-parse", "--verify", name], cwd=path, check=False)
        return result.returncode == 0
    except Exception:
        return False
