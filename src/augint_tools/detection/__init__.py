"""Shared detection engine for repo and workspace workflows."""

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
