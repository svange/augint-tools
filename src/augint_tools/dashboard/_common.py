"""GitHub auth/env helpers for the dashboard.

Mirrors the helpers that used to live in augint-github's ``common.py``.
Kept self-contained so the dashboard can ship as part of augint-tools without
depending on augint-github at runtime.
"""

import logging
import os
import subprocess
import sys
from types import FrameType

from dotenv import dotenv_values
from github import Auth, Github
from github.GithubException import UnknownObjectException
from github.Repository import Repository
from loguru import logger

# Stdlib loggers that PyGithub, urllib3, and Textual use to emit chatty
# request/retry records. They are silenced in the TUI (handlers never see
# them) but, when routed through :class:`InterceptHandler`, DEBUG records
# still reach the loguru file sink.
_CHATTY_STDLIB_LOGGERS: tuple[str, ...] = (
    "github",
    "github.Requester",
    "urllib3",
    "urllib3.connectionpool",
    "textual",
)


class InterceptHandler(logging.Handler):
    """Route stdlib ``logging`` records through loguru.

    Uses the standard loguru recipe (see loguru docs) so callers that rely
    on ``logging.getLogger(...)`` (PyGithub, urllib3, Textual) still end up
    in the configured loguru sinks -- critically the ``--log`` file.
    """

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - thin shim
        # Map stdlib level to loguru level; fall back to numeric level.
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find the caller frame so loguru reports the original call site
        # instead of this handler. Loosely follows the loguru docs recipe.
        frame: FrameType | None = logging.currentframe()
        depth = 2
        while frame is not None and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def configure_logging(verbose: bool, log_file: str | None = None) -> None:
    """Configure loguru: silent by default, compact format with --verbose.

    When ``log_file`` is given, DEBUG-level output is written to the file
    (safe to use alongside the TUI -- nothing goes to stderr).
    ``--verbose`` and ``--log`` can be combined.

    Also bridges stdlib ``logging`` into loguru via :class:`InterceptHandler`
    so PyGithub/urllib3/Textual records reach the ``--log`` file. The chatty
    stdlib loggers are held at WARNING so nothing flashes through Textual's
    own log capture, but DEBUG-level records still propagate to the loguru
    file sink because the InterceptHandler is installed at the root level 0.
    """
    logger.remove()
    if verbose:
        logger.add(sys.stderr, level="DEBUG", format="  {message}")
    if log_file:
        logger.add(
            log_file,
            level="DEBUG",
            format="{time:HH:mm:ss.SSS} | {level:<7} | {name}:{function}:{line} | {message}",
            rotation="5 MB",
            retention=2,
        )

    # Route stdlib logging into loguru. ``force=True`` replaces any
    # handlers Textual or other libs may have installed. ``level=0`` lets
    # every record through the root handler; per-logger levels below then
    # gate which records actually get emitted.
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

    # PyGithub's GithubRetry logs expected 403/404 responses at INFO level
    # via the stdlib logging module. Textual captures stdlib logging and
    # renders it in the TUI, causing distracting "Request GET ... failed
    # with 403: Forbidden" messages to flash on screen. Holding these
    # loggers at WARNING stops the flashing at the source. The loguru file
    # sink still sees whatever propagates through the InterceptHandler.
    for name in _CHATTY_STDLIB_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def _load_dotenv_values(filename: str = ".env") -> dict[str, str]:
    """Read key/value pairs from ``filename`` without mutating the process environment."""
    values = dotenv_values(filename)
    return {key: value for key, value in values.items() if value is not None}


def load_env_config(filename: str = ".env") -> tuple[str, str, str]:
    """Return GH_* configuration with explicit environment variables taking precedence."""
    env_values = _load_dotenv_values(filename)
    gh_repo = os.environ.get("GH_REPO", env_values.get("GH_REPO", ""))
    gh_account = os.environ.get("GH_ACCOUNT", env_values.get("GH_ACCOUNT", ""))
    gh_token = os.environ.get("GH_TOKEN", env_values.get("GH_TOKEN", ""))
    return gh_repo, gh_account, gh_token


def _get_gh_cli_token() -> str:
    """Return the token from ``gh auth token`` or an empty string if unavailable."""
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


def _resolve_token(filename: str = ".env", auth_source: str = "auto") -> str:
    """Return a GitHub token from the configured auth source."""
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
    """Get the GitHub repository object.

    Tries user lookup first, falls back to organization.
    """
    token = _resolve_token(auth_source=auth_source)
    auth = Auth.Token(token)
    g = Github(auth=auth)
    try:
        repo = g.get_user(github_account).get_repo(github_repo_name)
    except UnknownObjectException as e:
        logger.critical(e)
        repo = g.get_organization(github_account).get_repo(github_repo_name)
        logger.critical("You must add GH_USER to your env file.")

    return repo


def get_github_client(auth_source: str = "auto") -> Github:
    """Create an authenticated Github client from env, ``gh auth token``, or ``.env``."""
    token = _resolve_token(auth_source=auth_source)
    auth = Auth.Token(token)
    return Github(auth=auth)
