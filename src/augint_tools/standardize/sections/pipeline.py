"""Pipeline section checker: CI/CD workflow configuration."""

from pathlib import Path

from augint_tools.detection.engine import RepoContext
from augint_tools.standardize.models import Finding


def check_pipeline(path: Path, context: RepoContext) -> list[Finding]:
    """Check CI/CD pipeline configuration."""
    findings = []

    workflows_dir = path / ".github" / "workflows"
    if not workflows_dir.exists():
        findings.append(
            Finding(
                id="pipeline.workflows.missing",
                section="pipeline",
                severity="error",
                subject=".github/workflows/",
                actual="missing",
                expected="GitHub Actions workflows directory",
                can_fix=True,
                fix_kind="generate",
                source="pipeline standard",
            )
        )
        return findings

    # Check for a pipeline workflow
    pipeline_files = list(workflows_dir.glob("pipeline.*")) + list(workflows_dir.glob("ci.*"))
    if not pipeline_files:
        # Check for any workflow
        all_workflows = list(workflows_dir.glob("*.yml")) + list(workflows_dir.glob("*.yaml"))
        if not all_workflows:
            findings.append(
                Finding(
                    id="pipeline.workflow.missing",
                    section="pipeline",
                    severity="error",
                    subject=".github/workflows/",
                    actual="no workflow files found",
                    expected="at least one CI workflow",
                    can_fix=True,
                    fix_kind="generate",
                    source="pipeline standard",
                )
            )
        return findings

    # Check pipeline content
    for pf in pipeline_files:
        content = pf.read_text()

        # Check for expected job names (language-specific)
        if context.language == "python":
            if (
                "Code quality" not in content
                and "code-quality" not in content
                and "lint" not in content.lower()
            ):
                findings.append(
                    Finding(
                        id="pipeline.job_name.code_quality",
                        section="pipeline",
                        severity="warning",
                        subject=str(pf.relative_to(path)),
                        actual="no code quality job found",
                        expected="Code quality job present",
                        can_fix=False,
                        fix_kind="manual",
                        source="pipeline standard",
                    )
                )

            if "test" not in content.lower() and "pytest" not in content:
                findings.append(
                    Finding(
                        id="pipeline.job.tests",
                        section="pipeline",
                        severity="warning",
                        subject=str(pf.relative_to(path)),
                        actual="no test job found",
                        expected="test job present",
                        can_fix=False,
                        fix_kind="manual",
                        source="pipeline standard",
                    )
                )

    return findings
