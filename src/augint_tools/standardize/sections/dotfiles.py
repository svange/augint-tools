"""Dotfiles section checker: .editorconfig, .gitignore."""

from pathlib import Path

from augint_tools.detection.engine import RepoContext
from augint_tools.standardize.models import Finding


def check_dotfiles(path: Path, context: RepoContext) -> list[Finding]:
    """Check for expected dotfiles."""
    findings = []

    # .editorconfig
    if not (path / ".editorconfig").exists():
        findings.append(
            Finding(
                id="dotfiles.editorconfig.missing",
                section="dotfiles",
                severity="warning",
                subject=".editorconfig",
                actual="missing",
                expected="present",
                can_fix=True,
                fix_kind="generate",
                source="dotfiles standard",
            )
        )

    # .gitignore
    gitignore = path / ".gitignore"
    if not gitignore.exists():
        findings.append(
            Finding(
                id="dotfiles.gitignore.missing",
                section="dotfiles",
                severity="error",
                subject=".gitignore",
                actual="missing",
                expected="present",
                can_fix=True,
                fix_kind="generate",
                source="dotfiles standard",
            )
        )
    else:
        content = gitignore.read_text()
        # Check for .env exclusion
        if ".env" not in content:
            findings.append(
                Finding(
                    id="dotfiles.gitignore.env",
                    section="dotfiles",
                    severity="error",
                    subject=".gitignore",
                    actual=".env not in .gitignore",
                    expected=".env excluded",
                    can_fix=True,
                    fix_kind="patch",
                    source="dotfiles standard",
                )
            )

    return findings
