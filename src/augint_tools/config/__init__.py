"""Shared augint config home (~/.augint/) and layered .env loading.

All augint-* tools follow the same layered .env convention:

1. ``~/.augint/.env`` — global defaults (API keys, model prefs, shared tokens)
2. ``<repo>/.env``   — per-repo overrides
3. Process env vars  — highest priority (set by shell, CI, direnv, etc.)
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

AUGINT_HOME_ENV = "AUGINT_HOME"

_GITHUB_REMOTE_RE = re.compile(r"github\.com[:/]([^/]+)/([^/\n]+?)(?:\.git)?$")


def get_augint_home() -> Path:
    """Return the shared augint config directory.

    Checks ``AUGINT_HOME`` env var first, falls back to ``~/.augint/``.
    Used by dashboard (deployments.yaml) and team-secrets (teams.yaml, keys/).
    """
    override = os.environ.get(AUGINT_HOME_ENV)
    if override:
        return Path(override)
    return Path.home() / ".augint"


def load_augint_env(local_env: str = ".env") -> None:
    """Load layered .env into the process environment.

    Loads ``~/.augint/.env`` first, then ``local_env`` on top (overrides).
    Matches original ``load_dotenv(override=True)`` behaviour used by sync.py.
    """
    global_env = get_augint_home() / ".env"
    if global_env.is_file():
        load_dotenv(str(global_env), override=True)
    if Path(local_env).is_file():
        load_dotenv(str(local_env), override=True)


def augint_env_values(local_env: str = ".env") -> dict[str, str]:
    """Return merged key/value pairs from layered .env files.

    Does NOT mutate the process environment. Local values override global.
    Callers that also need process env precedence should check
    ``os.environ.get()`` first (auth.py already does this).
    """
    global_env = get_augint_home() / ".env"
    merged: dict[str, str] = {}
    if global_env.is_file():
        for k, v in dotenv_values(str(global_env)).items():
            if v is not None:
                merged[k] = v
    for k, v in dotenv_values(str(local_env)).items():
        if v is not None:
            merged[k] = v
    return merged


def detect_github_remote() -> tuple[str, str] | None:
    """Infer (account, repo) from the git remote origin URL.

    Returns None when not in a git repo or when origin isn't a GitHub URL.
    """
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    m = _GITHUB_REMOTE_RE.search(result.stdout.strip())
    if m:
        return m.group(1), m.group(2)
    return None


__all__ = ["augint_env_values", "detect_github_remote", "get_augint_home", "load_augint_env"]
