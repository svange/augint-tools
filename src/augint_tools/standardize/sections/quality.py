"""Quality section checker: pre-commit hooks, linter config."""

from pathlib import Path

from augint_tools.detection.engine import RepoContext
from augint_tools.standardize.models import Finding


def check_quality(path: Path, context: RepoContext) -> list[Finding]:
    """Check quality tooling configuration."""
    findings: list[Finding] = []

    if context.language == "python":
        _check_python_quality(path, findings)
    elif context.language == "typescript":
        _check_typescript_quality(path, findings)

    return findings


def _check_python_quality(path: Path, findings: list[Finding]) -> None:
    """Check Python quality tooling."""
    # pre-commit config
    precommit = path / ".pre-commit-config.yaml"
    if not precommit.exists():
        findings.append(
            Finding(
                id="quality.precommit.missing",
                section="quality",
                severity="error",
                subject=".pre-commit-config.yaml",
                actual="missing",
                expected="present with standard hooks",
                can_fix=True,
                fix_kind="generate",
                source="python quality standard",
            )
        )
    else:
        content = precommit.read_text()
        # Check for essential hooks
        if "ruff" not in content:
            findings.append(
                Finding(
                    id="quality.precommit.ruff",
                    section="quality",
                    severity="warning",
                    subject=".pre-commit-config.yaml",
                    actual="ruff hook not found",
                    expected="ruff format and check hooks present",
                    can_fix=True,
                    fix_kind="patch",
                    source="python quality standard",
                )
            )
        if "mypy" not in content:
            findings.append(
                Finding(
                    id="quality.precommit.mypy",
                    section="quality",
                    severity="warning",
                    subject=".pre-commit-config.yaml",
                    actual="mypy hook not found",
                    expected="mypy type checking hook present",
                    can_fix=False,
                    fix_kind="manual",
                    source="python quality standard",
                )
            )

    # pyproject.toml ruff config
    pyproject = path / "pyproject.toml"
    if pyproject.exists():
        content = pyproject.read_text()
        if "[tool.ruff]" not in content:
            findings.append(
                Finding(
                    id="quality.ruff.config",
                    section="quality",
                    severity="warning",
                    subject="pyproject.toml",
                    actual="no [tool.ruff] section",
                    expected="ruff configuration present",
                    can_fix=False,
                    fix_kind="manual",
                    source="python quality standard",
                )
            )


def _check_typescript_quality(path: Path, findings: list[Finding]) -> None:
    """Check TypeScript quality tooling."""
    biome = path / "biome.json"
    biome_c = path / "biome.jsonc"
    eslint = path / ".eslintrc.json"
    eslint_js = path / ".eslintrc.js"

    if not any(f.exists() for f in [biome, biome_c, eslint, eslint_js]):
        findings.append(
            Finding(
                id="quality.linter.missing",
                section="quality",
                severity="error",
                subject="linter config",
                actual="no linter config found",
                expected="biome or eslint configured",
                can_fix=False,
                fix_kind="manual",
                source="typescript quality standard",
            )
        )
