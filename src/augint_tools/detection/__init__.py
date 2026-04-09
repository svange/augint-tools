"""Shared detection engine for repo, workspace, and standardize workflows."""

from augint_tools.detection.engine import (
    CommandPlan,
    GitHubState,
    RepoContext,
    ToolchainInfo,
    detect,
)

__all__ = [
    "CommandPlan",
    "GitHubState",
    "RepoContext",
    "ToolchainInfo",
    "detect",
]
