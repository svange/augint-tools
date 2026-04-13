"""Command plan resolution from ecosystem defaults."""

from dataclasses import dataclass

from augint_tools.detection.toolchain import ToolchainInfo


@dataclass
class CommandPlan:
    """Resolved commands for each validation phase."""

    quality: str | None = None
    tests: str | None = None
    security: str | None = None
    licenses: str | None = None
    build: str | None = None


def resolve_command_plan(toolchain: ToolchainInfo, language: str) -> CommandPlan:
    """Resolve the command plan from ecosystem defaults."""
    if language == "python":
        return _python_defaults(toolchain)
    elif language in ("typescript", "mixed"):
        return _typescript_defaults(toolchain)
    return CommandPlan()


def _python_defaults(toolchain: ToolchainInfo) -> CommandPlan:
    """Default command plan for Python projects."""
    prefix = "uv run " if toolchain.package_manager == "uv" else ""

    # Quality
    if toolchain.has_pre_commit:
        quality = f"{prefix}pre-commit run --all-files"
    elif toolchain.has_ruff:
        quality = f"{prefix}ruff check . && {prefix}ruff format --check ."
    else:
        quality = None

    # Tests
    tests = f"{prefix}pytest -v" if toolchain.has_pytest else None

    # Security
    security = f"{prefix}pip-audit" if toolchain.has_pip_audit else None

    # Licenses
    licenses = (
        f"{prefix}pip-licenses --from=mixed --summary" if toolchain.has_pip_licenses else None
    )

    # Build
    build = "uv build" if toolchain.package_manager == "uv" else None

    return CommandPlan(
        quality=quality,
        tests=tests,
        security=security,
        licenses=licenses,
        build=build,
    )


def _typescript_defaults(toolchain: ToolchainInfo) -> CommandPlan:
    """Default command plan for TypeScript/JavaScript projects."""
    if toolchain.has_biome:
        quality = "npx biome check ."
    elif toolchain.has_npm:
        quality = "npm run lint"
    else:
        quality = None

    tests = "npm test" if toolchain.has_npm else None

    return CommandPlan(
        quality=quality,
        tests=tests,
        security="npm audit" if toolchain.has_npm else None,
        licenses=None,
        build="npm run build" if toolchain.has_npm else None,
    )
