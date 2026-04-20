"""Tests for dashboard logging setup (``configure_logging``, ``InterceptHandler``).

Bug context: stdlib log records from PyGithub/urllib3/Textual were flashing at
the top of the TUI because they were neither silenced at the logger level nor
bridged into loguru's file sink. These tests lock in both behaviors.
"""

from __future__ import annotations

import logging

from loguru import logger

from augint_tools.dashboard._common import (
    _CHATTY_STDLIB_LOGGERS,
    InterceptHandler,
    configure_logging,
)


def test_intercept_handler_bridges_stdlib_to_loguru():
    """A stdlib ``logging`` call should produce a loguru record.

    We install an InterceptHandler at the root, then sink loguru into an
    in-memory list so we can assert the record was forwarded.
    """
    captured: list[str] = []

    # Reset loguru, attach an in-memory sink, and wire stdlib -> loguru.
    logger.remove()
    sink_id = logger.add(lambda msg: captured.append(str(msg)), level="DEBUG")
    root = logging.getLogger()
    previous_handlers = list(root.handlers)
    previous_level = root.level
    root.handlers = [InterceptHandler()]
    root.setLevel(0)

    try:
        # Emit via stdlib at WARNING so it passes the default level gate.
        logging.getLogger("github").warning("forwarded-through-intercept")
        assert any("forwarded-through-intercept" in line for line in captured), (
            f"Expected loguru to receive the stdlib record; got: {captured!r}"
        )
    finally:
        logger.remove(sink_id)
        root.handlers = previous_handlers
        root.setLevel(previous_level)


def test_configure_logging_silences_chatty_loggers():
    """After ``configure_logging``, PyGithub/urllib3/Textual loggers are WARNING."""
    configure_logging(verbose=False, log_file=None)
    try:
        for name in _CHATTY_STDLIB_LOGGERS:
            assert logging.getLogger(name).level == logging.WARNING, (
                f"expected {name} to be silenced to WARNING"
            )
    finally:
        logger.remove()


def test_configure_logging_installs_intercept_handler():
    """Root logger must have our InterceptHandler wired up at level 0."""
    configure_logging(verbose=False, log_file=None)
    try:
        root = logging.getLogger()
        assert any(isinstance(h, InterceptHandler) for h in root.handlers), (
            f"expected InterceptHandler on root; got {root.handlers!r}"
        )
        assert root.level == 0
    finally:
        logger.remove()


def test_configure_logging_writes_log_file(tmp_path):
    """End-to-end: stdlib WARNING from ``github`` lands in the ``--log`` file.

    This is the smoke test called out in the bug report -- confirms
    ``--log ./tui.log`` actually produces a non-empty file for real traffic.
    """
    log_path = tmp_path / "tui.log"
    configure_logging(verbose=False, log_file=str(log_path))
    try:
        logging.getLogger("github").warning("smoke-test-request-failed")
        # Loguru flushes on each write; remove sinks to be safe.
    finally:
        logger.remove()

    assert log_path.exists(), "log file was not created"
    text = log_path.read_text(encoding="utf-8")
    assert text.strip(), "log file is empty"
    assert "smoke-test-request-failed" in text
