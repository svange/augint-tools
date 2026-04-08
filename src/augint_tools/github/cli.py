"""GitHub CLI wrapper."""

import subprocess


def run_gh(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """
    Run a gh CLI command.

    Args:
        args: Command arguments
        check: Whether to raise on non-zero exit

    Returns:
        CompletedProcess result
    """
    return subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        check=check,
    )


def is_gh_available() -> bool:
    """Check if gh CLI is installed and available."""
    try:
        result = run_gh(["--version"], check=False)
        return result.returncode == 0
    except FileNotFoundError:
        return False
    except Exception:
        return False


def is_gh_authenticated() -> bool:
    """Check if gh CLI is authenticated."""
    try:
        result = run_gh(["auth", "status"], check=False)
        return result.returncode == 0
    except Exception:
        return False
