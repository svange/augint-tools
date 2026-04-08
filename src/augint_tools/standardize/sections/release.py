"""Release section checker: semantic-release configuration."""

import tomllib
from pathlib import Path

from augint_tools.detection.engine import RepoContext
from augint_tools.standardize.models import Finding


def check_release(path: Path, context: RepoContext) -> list[Finding]:
    """Check semantic-release configuration."""
    findings: list[Finding] = []

    if context.language != "python":
        return findings  # Only check Python repos for now

    pyproject = path / "pyproject.toml"
    if not pyproject.exists():
        return findings

    try:
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
    except Exception:
        return findings

    sr = data.get("tool", {}).get("semantic_release", {})
    if not sr:
        findings.append(
            Finding(
                id="release.semantic_release.missing",
                section="release",
                severity="warning",
                subject="pyproject.toml",
                actual="no [tool.semantic_release] section",
                expected="semantic-release configured",
                can_fix=False,
                fix_kind="manual",
                source="release standard",
            )
        )
        return findings

    # Check tag format
    tag_format = sr.get("tag_format", "")
    project_name = data.get("project", {}).get("name", "")
    if project_name and tag_format and project_name not in tag_format:
        findings.append(
            Finding(
                id="release.tag_format.prefix",
                section="release",
                severity="warning",
                subject="pyproject.toml",
                actual=f"tag_format={tag_format}",
                expected=f"tag_format includes project name ({project_name})",
                can_fix=False,
                fix_kind="manual",
                source="release standard",
            )
        )

    # Check commit_parser
    if sr.get("commit_parser") != "conventional":
        findings.append(
            Finding(
                id="release.commit_parser",
                section="release",
                severity="warning",
                subject="pyproject.toml",
                actual=f"commit_parser={sr.get('commit_parser', 'not set')}",
                expected="commit_parser=conventional",
                can_fix=True,
                fix_kind="patch",
                source="release standard",
            )
        )

    return findings
