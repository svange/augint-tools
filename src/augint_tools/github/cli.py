"""GitHub CLI wrapper.

All ``gh`` subprocess calls route through :func:`run_gh`, which prefers the
user's keyring/SSO credentials over ``GH_TOKEN`` from the process environment.
Users keep ``GH_TOKEN`` in ``.env`` so it syncs to GitHub Actions secrets;
direnv-style shells auto-export that into the environment. Without this
sanitization, ``gh`` would silently use the (often narrower) .env token and
fail on operations that the user's SSO credentials would have authorized.
"""

import os
import subprocess
from functools import lru_cache

_GH_TOKEN_ENV_VARS = ("GH_TOKEN", "GITHUB_TOKEN")


def _env_without_gh_tokens() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in _GH_TOKEN_ENV_VARS}


@lru_cache(maxsize=1)
def _keyring_works() -> bool:
    """Return True if ``gh auth status`` succeeds using the keyring alone.

    Probes with GH_TOKEN/GITHUB_TOKEN stripped so ``gh`` can't short-circuit
    to the env var. Cached per process because ``gh auth status`` is slow
    enough to notice if called on every subprocess.
    """
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            env=_env_without_gh_tokens(),
            check=False,
        )
    except (FileNotFoundError, OSError):
        return False
    return result.returncode == 0


def _gh_env() -> dict[str, str]:
    if _keyring_works():
        return _env_without_gh_tokens()
    return os.environ.copy()


def run_gh(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run a ``gh`` command with keyring-first env sanitization."""
    return subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        env=_gh_env(),
        check=check,
    )


def is_gh_available() -> bool:
    """Check if gh CLI is installed and available."""
    try:
        result = run_gh(["--version"], check=False)
        return result.returncode == 0
    except FileNotFoundError:
        return False
    except Exception:
        return False


def is_gh_authenticated() -> bool:
    """Check if gh CLI is authenticated by any available mechanism."""
    try:
        result = run_gh(["auth", "status"], check=False)
        return result.returncode == 0
    except Exception:
        return False
