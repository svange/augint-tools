"""Verify: re-run audit after fixes and confirm alignment."""

from pathlib import Path

from augint_tools.detection.engine import RepoContext
from augint_tools.standardize.audit import run_audit
from augint_tools.standardize.models import AuditResult


def verify(
    path: Path,
    context: RepoContext,
    profile_id: str,
    sections: list[str] | None = None,
) -> AuditResult:
    """Re-run audit after fixes to confirm alignment.

    This is identical to run_audit -- it exists as a named step
    so skills and commands have a clear verify phase.
    """
    return run_audit(path, context, profile_id, sections=sections)
