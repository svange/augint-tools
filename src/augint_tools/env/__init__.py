"""Environment variable management: classification, sync, and backup."""

from augint_tools.env.auth import get_github_client, get_github_repo, load_env_config, resolve_token
from augint_tools.env.classify import Classification, classify_env, classify_variable
from augint_tools.env.sync import perform_sync

__all__ = [
    "Classification",
    "classify_env",
    "classify_variable",
    "get_github_client",
    "get_github_repo",
    "load_env_config",
    "perform_sync",
    "resolve_token",
]
