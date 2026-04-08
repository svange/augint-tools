"""JSON and human-readable output formatting."""

import json
import sys
from datetime import UTC, datetime
from typing import Any

import click


def emit_json(data: dict[str, Any]) -> None:
    """Emit JSON output to stdout."""
    # Add timestamp if not present
    if "timestamp" not in data:
        data["timestamp"] = datetime.now(UTC).isoformat()

    click.echo(json.dumps(data, indent=2))


def emit_output(
    command: str,
    scope: str,
    as_json: bool = False,
    status: str = "ok",
    **data: Any,
) -> None:
    """
    Emit command output in JSON or human-readable format.

    Args:
        command: Command name (e.g., "status", "branch")
        scope: Command scope ("repo" or "monorepo")
        as_json: Whether to output JSON format
        status: Command status ("ok" or "error")
        **data: Additional data to include in response
    """
    response = {
        "command": command,
        "status": status,
        "scope": scope,
        **data,
    }

    if as_json:
        emit_json(response)
    else:
        _emit_human(response)


def _emit_human(data: dict[str, Any]) -> None:
    """Emit human-readable output."""
    command = data.get("command", "unknown")
    status_val = data.get("status", "ok")

    # Show status with color
    if status_val == "ok":
        status_str = click.style("✓", fg="green")
    elif status_val == "error":
        status_str = click.style("✗", fg="red")
    else:
        status_str = click.style("•", fg="yellow")

    click.echo(f"{status_str} {command}")

    # Show error if present
    if "error" in data:
        emit_error(data["error"])

    # Command-specific formatting
    if command == "status" and "repo" in data:
        _format_repo_status(data["repo"])
    elif command == "status" and "repos" in data:
        _format_workspace_status(data)
    elif command == "issues" and "issues" in data:
        _format_issues(data["issues"])
    elif command == "branch" and status_val == "ok":
        if "branch" in data:
            click.echo(f"  Branch: {click.style(data['branch'], fg='cyan')}")
    elif command == "foreach" and "results" in data:
        _format_foreach_results(data["results"])


def _format_repo_status(repo: dict[str, Any]) -> None:
    """Format single repo status."""
    click.echo(f"  Branch: {click.style(repo.get('branch', 'unknown'), fg='cyan')}")

    if repo.get("dirty"):
        dirty_files = repo.get("dirty_files", [])
        click.echo(f"  Status: {click.style('dirty', fg='yellow')} ({len(dirty_files)} files)")
    else:
        click.echo(f"  Status: {click.style('clean', fg='green')}")

    ahead = repo.get("ahead", 0)
    behind = repo.get("behind", 0)
    if ahead > 0 or behind > 0:
        click.echo(f"  Remote: ahead {ahead}, behind {behind}")

    # GitHub info
    if "open_prs" in repo and repo["open_prs"]:
        click.echo(f"  Open PRs: {len(repo['open_prs'])}")
        for pr in repo["open_prs"]:
            pr_num = pr.get("number", "?")
            pr_title = pr.get("title", "")
            click.echo(f"    #{pr_num}: {pr_title}")


def _format_workspace_status(data: dict[str, Any]) -> None:
    """Format workspace status."""
    workspace = data.get("workspace", {})
    repos = data.get("repos", [])

    click.echo(f"  Workspace: {workspace.get('name', 'unknown')}")
    click.echo(f"  Repositories: {len(repos)}")

    for repo in repos:
        name = repo.get("name", "unknown")
        if not repo.get("present"):
            click.echo(f"    {click.style(name, fg='red')} (missing)")
            continue

        branch = repo.get("branch", "?")
        status_icon = "•" if repo.get("dirty") else "✓"
        status_color = "yellow" if repo.get("dirty") else "green"

        click.echo(f"    {click.style(status_icon, fg=status_color)} {name} ({branch})")


def _format_issues(issues: list[dict[str, Any]]) -> None:
    """Format issues list."""
    if not issues:
        click.echo("  No issues found")
        return

    click.echo(f"  Found {len(issues)} issues:")
    for issue in issues:
        number = issue.get("number", "?")
        title = issue.get("title", "")
        labels = issue.get("labels", [])
        label_str = f" [{', '.join(labels)}]" if labels else ""
        click.echo(f"    #{number}: {title}{label_str}")


def _format_foreach_results(results: list[dict[str, Any]]) -> None:
    """Format foreach command results."""
    for result in results:
        repo = result.get("repo", "unknown")
        success = result.get("success", False)
        exit_code = result.get("exit_code", 0)

        if success:
            click.echo(f"  {click.style('✓', fg='green')} {repo}")
        else:
            click.echo(f"  {click.style('✗', fg='red')} {repo} (exit {exit_code})")

        if output := result.get("output"):
            for line in output.split("\n"):
                if line.strip():
                    click.echo(f"      {line}")


def emit_error(message: str, exit_code: int | None = None) -> None:
    """
    Emit error message to stderr.

    Args:
        message: Error message
        exit_code: Optional exit code (exits if provided)
    """
    click.echo(click.style(f"Error: {message}", fg="red"), err=True)
    if exit_code is not None:
        sys.exit(exit_code)


def emit_warning(message: str) -> None:
    """Emit warning message to stderr."""
    click.echo(click.style(f"Warning: {message}", fg="yellow"), err=True)


def create_error_response(
    command: str,
    scope: str,
    error: str,
    **extra: Any,
) -> dict[str, Any]:
    """Create error response dict."""
    return {
        "command": command,
        "status": "error",
        "scope": scope,
        "error": error,
        **extra,
    }
