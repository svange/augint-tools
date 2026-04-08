"""Renovate section checker."""

from pathlib import Path

from augint_tools.detection.engine import RepoContext
from augint_tools.standardize.models import Finding


def check_renovate(path: Path, context: RepoContext) -> list[Finding]:
    """Check Renovate dependency management configuration."""
    findings = []

    renovate_files = [
        path / "renovate.json5",
        path / "renovate.json",
        path / ".renovaterc",
        path / ".renovaterc.json",
        path / ".github" / "renovate.json5",
        path / ".github" / "renovate.json",
    ]

    if not any(f.exists() for f in renovate_files):
        findings.append(
            Finding(
                id="renovate.config.missing",
                section="renovate",
                severity="warning",
                subject="renovate.json5",
                actual="missing",
                expected="Renovate configuration present",
                can_fix=True,
                fix_kind="generate",
                source="renovate standard",
            )
        )

    return findings
