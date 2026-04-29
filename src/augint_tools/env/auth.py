"""GitHub authentication and env config loading.

Default token resolution (no ``--env``):
1. ``gh auth token`` (GitHub CLI keyring / SSO)
2. ``GH_TOKEN`` environment variable

With ``--env [file]``:
  Reads ``GH_TOKEN`` from the layered .env (``~/.augint/.env`` + ``[file]``).
"""

from __future__ import annotations

import os
import subprocess

from github import Auth, Github
from github.GithubException import UnknownObjectException
from github.Repository import Repository
from loguru import logger

from augint_tools.config import augint_env_values, detect_github_remote


def load_env_config(env_file: str | None = None) -> tuple[str, str, str]:
    """Return (GH_REPO, GH_ACCOUNT, GH_TOKEN).

    Resolution order for GH_REPO / GH_ACCOUNT:
    1. ``--env`` file values (if *env_file* provided)
    2. ``GH_REPO`` / ``GH_ACCOUNT`` environment variables
    3. Inferred from ``git remote origin``
    """
    if env_file:
        values = augint_env_values(env_file)
    else:
        values = {}

    gh_repo = values.get("GH_REPO", "") or os.environ.get("GH_REPO", "")
    gh_account = values.get("GH_ACCOUNT", "") or os.environ.get("GH_ACCOUNT", "")
    gh_token = values.get("GH_TOKEN", "") or os.environ.get("GH_TOKEN", "")

    if not gh_repo or not gh_account:
        remote = detect_github_remote()
        if remote:
            if not gh_account:
                gh_account = remote[0]
            if not gh_repo:
                gh_repo = remote[1]

    return gh_repo, gh_account, gh_token


def _get_gh_cli_token() -> str:
    """Return the token from ``gh auth token`` (keyring/SSO) or empty string.

    Strips GH_TOKEN/GITHUB_TOKEN from the subprocess env so ``gh`` reports the
    keyring token instead of echoing back whatever was already in the process
    environment (which may be a narrow .env token auto-exported by direnv).
    """
    env = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "GITHUB_TOKEN")}
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""
    return result.stdout.strip()


def resolve_token(env_file: str | None = None) -> str:
    """Return a GitHub token.

    With *env_file*: reads GH_TOKEN from the layered .env.
    Without: ``gh auth token`` -> ``GH_TOKEN`` env var -> error.
    """
    if env_file:
        token = augint_env_values(env_file).get("GH_TOKEN", "").strip()
        if token:
            logger.debug("Using GitHub token from .env (--env).")
            return token
        raise RuntimeError("No GH_TOKEN found in .env. Remove --env or add GH_TOKEN to the file.")

    gh_token = _get_gh_cli_token()
    if gh_token:
        logger.debug("Using GitHub token from gh auth token (keyring/SSO).")
        return gh_token

    env_token = os.environ.get("GH_TOKEN", "").strip()
    if env_token:
        logger.debug("Using GitHub token from GH_TOKEN environment variable.")
        return env_token

    raise RuntimeError(
        "No GitHub token found. Authenticate with 'gh auth login', "
        "set GH_TOKEN in the environment, or pass --env <file>."
    )


def get_github_repo(
    github_account: str,
    github_repo_name: str,
    env_file: str | None = None,
) -> Repository:
    """Get the GitHub repository object (tries user, falls back to org)."""
    token = resolve_token(env_file=env_file)
    auth = Auth.Token(token)
    g = Github(auth=auth)
    try:
        return g.get_user(github_account).get_repo(github_repo_name)
    except UnknownObjectException:
        return g.get_organization(github_account).get_repo(github_repo_name)


def get_github_client(env_file: str | None = None) -> Github:
    """Create an authenticated Github client."""
    token = resolve_token(env_file=env_file)
    auth = Auth.Token(token)
    return Github(auth=auth)
