"""Built-in health checks. Importing this module triggers registration."""

from . import broken_ci, open_issues, renovate, renovate_prs, repo_standards, stale_prs

__all__ = [
    "broken_ci",
    "open_issues",
    "renovate",
    "renovate_prs",
    "repo_standards",
    "stale_prs",
]
