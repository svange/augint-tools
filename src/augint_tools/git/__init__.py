"""Git operations utilities."""

from augint_tools.git.branch import branch_exists, create_branch, push_branch, switch_branch
from augint_tools.git.repo import (
    detect_base_branch,
    extract_repo_slug,
    get_current_branch,
    get_remote_url,
    is_git_repo,
    run_git,
)
from augint_tools.git.status import get_ahead_behind, get_dirty_files, get_repo_status

__all__ = [
    "is_git_repo",
    "get_current_branch",
    "get_remote_url",
    "extract_repo_slug",
    "detect_base_branch",
    "run_git",
    "get_dirty_files",
    "get_ahead_behind",
    "get_repo_status",
    "create_branch",
    "switch_branch",
    "push_branch",
    "branch_exists",
]
