"""Check plan execution."""

import time
from dataclasses import dataclass, field
from pathlib import Path

from augint_tools.checks.plan import CheckPlan
from augint_tools.execution.runner import run_command


@dataclass
class PhaseResult:
    """Result of executing a single check phase."""

    phase: str
    command: str
    status: str  # passed, failed, skipped, fixed
    exit_code: int = 0
    duration_seconds: float = 0.0
    failures: list[str] = field(default_factory=list)
    fixed_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "command": self.command,
            "status": self.status,
            "exit_code": self.exit_code,
            "duration_seconds": round(self.duration_seconds, 2),
            "failures": self.failures,
            "fixed_files": self.fixed_files,
        }


def run_plan(
    plan: CheckPlan,
    cwd: Path,
    *,
    fix: bool = False,
) -> list[PhaseResult]:
    """Execute each phase in order and return structured results.

    Args:
        plan: Resolved check plan.
        cwd: Working directory for command execution.
        fix: Attempt mechanical fixes for fixable phases.

    Returns:
        List of PhaseResult, one per phase.
    """
    results: list[PhaseResult] = []

    for check_phase in plan.phases:
        command = check_phase.command

        # For fixable phases with --fix, try to apply fixes
        if fix and check_phase.fixable:
            command = _apply_fix_flag(command)

        start = time.monotonic()
        cmd_result = run_command(command, cwd=cwd)
        duration = time.monotonic() - start

        if cmd_result.success:
            status = "fixed" if fix and check_phase.fixable else "passed"
        else:
            status = "failed"

        # Extract actionable failure lines from output
        failures = (
            _extract_failures(cmd_result.stderr or cmd_result.stdout)
            if not cmd_result.success
            else []
        )

        results.append(
            PhaseResult(
                phase=check_phase.name,
                command=check_phase.command,
                status=status,
                exit_code=cmd_result.exit_code,
                duration_seconds=duration,
                failures=failures,
            )
        )

    return results


def _apply_fix_flag(command: str) -> str:
    """Add fix flags to known tools."""
    if "ruff check" in command:
        return command.replace("ruff check", "ruff check --fix")
    if "pre-commit" in command:
        # pre-commit auto-fixes by default
        return command
    if "biome check" in command:
        return command.replace("biome check", "biome check --apply")
    return command


def _extract_failures(output: str) -> list[str]:
    """Extract actionable failure lines from command output.

    Keeps only lines that look like errors, not full command chatter.
    """
    lines = output.strip().split("\n")
    failures = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # Keep lines that look like errors
        if any(
            marker in stripped.lower()
            for marker in ["error", "failed", "fail", "exception", "FAILED"]
        ):
            failures.append(stripped)
    # Cap at 20 lines to avoid flooding
    return failures[:20]
