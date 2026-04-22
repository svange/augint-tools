"""Dashboard logging helpers.

Auth/env helpers live in :mod:`augint_tools.env.auth`; ``load_env_config`` and
``get_github_client`` are re-exported here so dashboard callers keep their
local import path.
"""

import logging
import sys
from types import FrameType

from loguru import logger

# Re-export the canonical auth helpers so dashboard code can keep importing
# from ._common (the dashboard used to carry its own copies before the env
# module existed).
from augint_tools.env.auth import get_github_client, get_github_repo, load_env_config

__all__ = [
    "InterceptHandler",
    "configure_logging",
    "get_github_client",
    "get_github_repo",
    "load_env_config",
]

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
