"""Command execution utilities."""

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CommandResult:
    """Result of command execution."""

    success: bool
    exit_code: int
    stdout: str
    stderr: str
    command: str


def run_command(
    cmd: str,
    cwd: Path | None = None,
    timeout: int | None = 300,
    shell: bool = True,
) -> CommandResult:
    """
    Run a shell command and capture output.

    Args:
        cmd: Command to run
        cwd: Working directory (defaults to current directory)
        timeout: Timeout in seconds (default: 300)
        shell: Whether to run in shell (default: True)

    Returns:
        CommandResult with execution details
    """
    try:
        result = subprocess.run(
            cmd,
            shell=shell,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

        return CommandResult(
            success=result.returncode == 0,
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            command=cmd,
        )
    except subprocess.TimeoutExpired:
        return CommandResult(
            success=False,
            exit_code=-1,
            stdout="",
            stderr=f"Command timed out after {timeout} seconds",
            command=cmd,
        )
    except Exception as e:
        return CommandResult(
            success=False,
            exit_code=-1,
            stdout="",
            stderr=str(e),
            command=cmd,
        )


def discover_test_command(cwd: Path | None = None) -> str | None:
    """
    Discover test command from common conventions.

    Args:
        cwd: Repository path

    Returns:
        Test command or None if not found
    """
    if cwd is None:
        cwd = Path.cwd()

    # Check for pytest
    if (cwd / "pytest.ini").exists() or (cwd / "pyproject.toml").exists():
        return "pytest -v"

    # Check for npm/package.json
    if (cwd / "package.json").exists():
        return "npm test"

    # Check for Makefile
    if (cwd / "Makefile").exists():
        return "make test"

    return None


def discover_lint_command(cwd: Path | None = None) -> str | None:
    """
    Discover lint command from common conventions.

    Args:
        cwd: Repository path

    Returns:
        Lint command or None if not found
    """
    if cwd is None:
        cwd = Path.cwd()

    # Check for pre-commit
    if (cwd / ".pre-commit-config.yaml").exists():
        return "pre-commit run --all-files"

    # Check for ruff
    if (cwd / "pyproject.toml").exists():
        return "ruff check ."

    # Check for npm/package.json
    if (cwd / "package.json").exists():
        return "npm run lint"

    # Check for Makefile
    if (cwd / "Makefile").exists():
        return "make lint"

    return None
