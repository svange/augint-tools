"""GitHub integration utilities."""

from augint_tools.github.cli import is_gh_authenticated, is_gh_available
from augint_tools.github.issues import Issue, list_issues
from augint_tools.github.prs import create_pr, enable_automerge, get_open_prs

__all__ = [
    "is_gh_available",
    "is_gh_authenticated",
    "Issue",
    "list_issues",
    "create_pr",
    "enable_automerge",
    "get_open_prs",
]
