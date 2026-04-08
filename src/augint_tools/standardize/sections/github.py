"""GitHub section checker: repo settings, rulesets, merge strategy."""

from pathlib import Path

from augint_tools.detection.engine import RepoContext
from augint_tools.github.cli import run_gh
from augint_tools.standardize.models import Finding


def check_github(path: Path, context: RepoContext) -> list[Finding]:
    """Check GitHub repository configuration."""
    findings = []

    if not context.github.authenticated or not context.github.repo_slug:
        findings.append(
            Finding(
                id="github.auth.missing",
                section="github",
                severity="info",
                subject="GitHub CLI",
                actual="not authenticated or no repo slug",
                expected="authenticated with repo access",
                can_fix=False,
                fix_kind="manual",
                source="github standard",
            )
        )
        return findings

    # Check repo settings via gh api
    try:
        result = run_gh(
            [
                "api",
                f"repos/{context.github.repo_slug}",
                "--jq",
                ".delete_branch_on_merge,.allow_merge_commit,.allow_squash_merge,.allow_rebase_merge",
            ],
            check=False,
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            if len(lines) >= 1 and lines[0] == "false":
                findings.append(
                    Finding(
                        id="github.delete_branch_on_merge",
                        section="github",
                        severity="warning",
                        subject=context.github.repo_slug,
                        actual="delete_branch_on_merge disabled",
                        expected="enabled",
                        can_fix=True,
                        fix_kind="external",
                        source="github standard",
                    )
                )
    except Exception:
        pass

    return findings
