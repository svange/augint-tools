"""Audit orchestrator: runs section checkers and collects findings."""

from pathlib import Path

from augint_tools.detection.engine import RepoContext
from augint_tools.standardize.models import AuditResult, AuditSummary, SectionResult
from augint_tools.standardize.sections import SECTION_CHECKERS

ALL_SECTIONS = list(SECTION_CHECKERS.keys())


def run_audit(
    path: Path,
    context: RepoContext,
    profile_id: str,
    sections: list[str] | None = None,
) -> AuditResult:
    """Run all section checks and collect findings.

    Args:
        path: Repository root.
        context: Detection context.
        profile_id: Resolved profile identifier.
        sections: Specific sections to check, or None for all.

    Returns:
        Complete audit result with findings and summary.
    """
    target_sections = sections if sections else ALL_SECTIONS

    result = AuditResult(profile_id=profile_id)
    total_errors = 0
    total_warnings = 0
    sections_passing = 0

    for section_name in target_sections:
        checker = SECTION_CHECKERS.get(section_name)
        if checker is None:
            result.sections[section_name] = SectionResult(
                section=section_name,
                status="skipped",
            )
            continue

        findings = checker(path, context)
        result.findings.extend(findings)

        errors = sum(1 for f in findings if f.severity == "error")
        warnings = sum(1 for f in findings if f.severity == "warning")
        total_errors += errors
        total_warnings += warnings

        if errors == 0 and warnings == 0:
            status = "pass"
            sections_passing += 1
        elif errors > 0:
            status = "drift"
        else:
            status = "drift"

        result.sections[section_name] = SectionResult(
            section=section_name,
            status=status,
            error_count=errors,
            warning_count=warnings,
        )

    result.summary = AuditSummary(
        total_errors=total_errors,
        total_warnings=total_warnings,
        sections_checked=len(target_sections),
        sections_passing=sections_passing,
        fixable_count=sum(1 for f in result.findings if f.can_fix),
    )

    return result
