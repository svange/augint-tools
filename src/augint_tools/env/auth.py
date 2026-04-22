"""GitHub authentication and env config loading.

Token resolution priority (auth_source="auto"):
1. gh auth token (GitHub CLI keyring / SSO)
2. GH_TOKEN environment variable
3. GH_TOKEN from .env file

Keyring wins by design: users keep GH_TOKEN in .env so it syncs to GitHub
Actions secrets, but those tokens are often narrower-scope than their SSO
credentials. Preferring the keyring avoids silently downgrading auth when
shells (direnv/autoenv) auto-export .env into the process environment.
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

    gh_token = _get_gh_cli_token()
    if gh_token:
        logger.debug("Using GitHub token from gh auth token (keyring/SSO).")
        return gh_token

    env_token = os.environ.get("GH_TOKEN", "").strip()
    if env_token:
        logger.debug(
            "Using GitHub token from GH_TOKEN environment variable "
            "(gh CLI keyring unavailable)."
        )
        return env_token

    if dotenv_token:
        logger.debug("Using GitHub token from GH_TOKEN in .env (keyring unavailable).")
        return dotenv_token

    raise RuntimeError(
        "No GitHub token found. Authenticate with 'gh auth login', "
        "or set GH_TOKEN in .env / environment."
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
