"""GitHub authentication and env config loading.

Token resolution priority (auth_source="auto"):
1. GH_TOKEN environment variable
2. gh auth token (GitHub CLI keyring)
3. GH_TOKEN from .env file
"""

from __future__ import annotations

import os
import subprocess

from dotenv import dotenv_values
from github import Auth, Github
from github.GithubException import UnknownObjectException
from github.Repository import Repository
from loguru import logger


def _load_dotenv_values(filename: str = ".env") -> dict[str, str]:
    """Read key/value pairs from *filename* without mutating the process environment."""
    values = dotenv_values(filename)
    return {key: value for key, value in values.items() if value is not None}


def load_env_config(filename: str = ".env") -> tuple[str, str, str]:
    """Return (GH_REPO, GH_ACCOUNT, GH_TOKEN) with env vars taking precedence over .env."""
    env_values = _load_dotenv_values(filename)
    gh_repo = os.environ.get("GH_REPO", env_values.get("GH_REPO", ""))
    gh_account = os.environ.get("GH_ACCOUNT", env_values.get("GH_ACCOUNT", ""))
    gh_token = os.environ.get("GH_TOKEN", env_values.get("GH_TOKEN", ""))
    return gh_repo, gh_account, gh_token


def _get_gh_cli_token() -> str:
    """Return the token from ``gh auth token`` or empty string if unavailable."""
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""
    return result.stdout.strip()


def resolve_token(filename: str = ".env", auth_source: str = "auto") -> str:
    """Return a GitHub token from the configured auth source.

    Raises RuntimeError if no token can be found.
    """
    dotenv_token = _load_dotenv_values(filename).get("GH_TOKEN", "").strip()

    if auth_source == "dotenv":
        if dotenv_token:
            logger.debug("Using GitHub token from GH_TOKEN in .env (--env-auth).")
            return dotenv_token
        raise RuntimeError(
            "No GitHub token found in .env. Remove --env-auth or add GH_TOKEN to .env."
        )

    if auth_source != "auto":
        raise ValueError(f"Unsupported auth_source '{auth_source}'.")

    token = os.environ.get("GH_TOKEN", "").strip()
    if token:
        logger.debug("Using GitHub token from GH_TOKEN environment variable.")
        return token

    gh_token = _get_gh_cli_token()
    if gh_token:
        if dotenv_token:
            logger.debug(
                "Using GitHub token from gh auth token. Ignoring GH_TOKEN from .env; "
                "export GH_TOKEN in the current shell to force it."
            )
        else:
            logger.debug("Using GitHub token from gh auth token.")
        return gh_token

    if dotenv_token:
        logger.debug("Using GitHub token from GH_TOKEN in .env.")
        return dotenv_token

    raise RuntimeError(
        "No GitHub token found. Set GH_TOKEN in .env / environment, "
        "or authenticate with: gh auth login"
    )


def get_github_repo(
    github_account: str,
    github_repo_name: str,
    auth_source: str = "auto",
) -> Repository:
    """Get the GitHub repository object (tries user, falls back to org)."""
    token = resolve_token(auth_source=auth_source)
    auth = Auth.Token(token)
    g = Github(auth=auth)
    try:
        return g.get_user(github_account).get_repo(github_repo_name)
    except UnknownObjectException:
        return g.get_organization(github_account).get_repo(github_repo_name)


def get_github_client(auth_source: str = "auto") -> Github:
    """Create an authenticated Github client."""
    token = resolve_token(auth_source=auth_source)
    auth = Auth.Token(token)
    return Github(auth=auth)
