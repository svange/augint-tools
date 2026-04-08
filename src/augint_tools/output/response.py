"""Standardized command response model and exit codes."""

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class ExitCode(IntEnum):
    """Consistent exit codes per spec."""

    SUCCESS = 0
    FAILURE = 1
    ACTION_REQUIRED = 2
    BLOCKED = 3
    PARTIAL = 4


STATUS_EXIT_MAP: dict[str, ExitCode] = {
    "ok": ExitCode.SUCCESS,
    "error": ExitCode.FAILURE,
    "action-required": ExitCode.ACTION_REQUIRED,
    "blocked": ExitCode.BLOCKED,
    "partial": ExitCode.PARTIAL,
}


@dataclass
class CommandResponse:
    """Canonical output envelope for every AI-facing command.

    Every command must construct one of these and pass it to emit_response().
    """

    command: str
    scope: str
    status: str
    summary: str
    result: dict[str, Any] = field(default_factory=dict)
    next_actions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return STATUS_EXIT_MAP.get(self.status, ExitCode.FAILURE)

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "scope": self.scope,
            "status": self.status,
            "summary": self.summary,
            "next_actions": self.next_actions,
            "warnings": self.warnings,
            "errors": self.errors,
            "result": self.result,
        }

    @staticmethod
    def error(
        command: str,
        scope: str,
        error: str,
        **result_kwargs: Any,
    ) -> "CommandResponse":
        """Convenience constructor for error responses."""
        return CommandResponse(
            command=command,
            scope=scope,
            status="error",
            summary=error,
            errors=[error],
            result=dict(result_kwargs),
        )

    @staticmethod
    def ok(
        command: str,
        scope: str,
        summary: str,
        result: dict[str, Any] | None = None,
        next_actions: list[str] | None = None,
    ) -> "CommandResponse":
        """Convenience constructor for success responses."""
        return CommandResponse(
            command=command,
            scope=scope,
            status="ok",
            summary=summary,
            result=result or {},
            next_actions=next_actions or [],
        )
