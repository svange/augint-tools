"""Git status checking operations."""

from dataclasses import dataclass
from pathlib import Path

from augint_tools.git.repo import run_git


@dataclass
class RepoStatus:
    """Repository status information."""

    branch: str | None
    dirty: bool
    dirty_files: list[str]
    ahead: int
    behind: int


def get_dirty_files(path: Path | None = None) -> list[str]:
    """
    Get list of dirty (modified/untracked) files.

    Args:
        path: Repository path (defaults to current directory)

    Returns:
        List of file paths
    """
    try:
        result = run_git(["status", "--porcelain"], cwd=path, check=False)
        if result.returncode != 0:
            return []

        files = []
        for line in result.stdout.split("\n"):
            if line.strip():
                # Format is "XY filename", we just want the filename
                files.append(line[3:].strip())
        return files
    except Exception:
        return []


def get_ahead_behind(path: Path | None = None, remote_branch: str | None = None) -> tuple[int, int]:
    """
    Get ahead/behind count relative to remote branch.

    Args:
        path: Repository path (defaults to current directory)
        remote_branch: Remote branch to compare (e.g., "origin/main")
                      If None, uses tracking branch

    Returns:
        Tuple of (ahead, behind) counts
    """
    try:
        # If no remote branch specified, try to get tracking branch
        if remote_branch is None:
            result = run_git(
                ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
                cwd=path,
                check=False,
            )
            if result.returncode != 0:
                return (0, 0)
            remote_branch = result.stdout.strip()

        # Get ahead/behind counts
        result = run_git(
            ["rev-list", "--left-right", "--count", f"HEAD...{remote_branch}"],
            cwd=path,
            check=False,
        )
        if result.returncode != 0:
            return (0, 0)

        parts = result.stdout.strip().split()
        if len(parts) == 2:
            return (int(parts[0]), int(parts[1]))
        return (0, 0)
    except Exception:
        return (0, 0)


def get_repo_status(path: Path | None = None, branch: str | None = None) -> RepoStatus:
    """
    Get comprehensive repository status.

    Args:
        path: Repository path (defaults to current directory)
        branch: Current branch (will be detected if None)

    Returns:
        RepoStatus object
    """
    from augint_tools.git.repo import get_current_branch

    if branch is None:
        branch = get_current_branch(path)

    dirty_files = get_dirty_files(path)
    ahead, behind = get_ahead_behind(path)

    return RepoStatus(
        branch=branch,
        dirty=len(dirty_files) > 0,
        dirty_files=dirty_files,
        ahead=ahead,
        behind=behind,
    )
