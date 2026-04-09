"""JSON and human-readable output formatting."""

import json
import sys
from datetime import UTC, datetime
from typing import Any

import click

from augint_tools.output.response import CommandResponse


def emit_response(
    response: CommandResponse,
    *,
    json_mode: bool = False,
    actionable: bool = False,
    summary_only: bool = False,
) -> None:
    """Emit a CommandResponse in JSON or human-readable format.

    Args:
        response: The response to emit.
        json_mode: Output as JSON.
        actionable: Suppress passing/no-op items.
        summary_only: Emit only status, summary, and next_actions.
    """
    if actionable and response.status == "ok" and not response.warnings:
        return

    data = response.to_dict()

    if json_mode:
        if summary_only:
            data = {
                "command": data["command"],
                "scope": data["scope"],
                "status": data["status"],
                "summary": data["summary"],
                "next_actions": data["next_actions"],
            }
        _emit_json(data)
    else:
        if summary_only:
            _emit_summary(response)
        else:
            _emit_human(response)


def _emit_json(data: dict[str, Any]) -> None:
    """Emit JSON output to stdout with timestamp."""
    if "timestamp" not in data:
        data["timestamp"] = datetime.now(UTC).isoformat()
    click.echo(json.dumps(data, indent=2))


def _emit_summary(response: CommandResponse) -> None:
    """Emit only the summary line and next actions."""
    status_str = _status_icon(response.status)
    click.echo(f"{status_str} {response.summary}")
    if response.next_actions:
        click.echo(f"  Next: {', '.join(response.next_actions)}")


def _emit_human(response: CommandResponse) -> None:
    """Emit human-readable output using registry-based formatters."""
    status_str = _status_icon(response.status)
    click.echo(f"{status_str} {response.command}: {response.summary}")

    # Show warnings
    for warning in response.warnings:
        emit_warning(warning)

    # Show errors
    for error in response.errors:
        click.echo(click.style(f"  {error}", fg="red"), err=True)

    # Delegate to registered formatter if available
    key = response.command
    if key in _HUMAN_FORMATTERS:
        _HUMAN_FORMATTERS[key](response.result)

    # Show next actions
    if response.next_actions:
        click.echo(f"  Next: {', '.join(response.next_actions)}")


def _status_icon(status: str) -> str:
    if status == "ok":
        return click.style("[ok]", fg="green")
    elif status == "error":
        return click.style("[error]", fg="red")
    elif status == "action-required":
        return click.style("[action]", fg="yellow")
    elif status == "blocked":
        return click.style("[blocked]", fg="red")
    elif status == "partial":
        return click.style("[partial]", fg="yellow")
    else:
        return click.style(f"[{status}]", fg="yellow")


# --- Registry-based human formatters ---
# Each formatter receives response.result and prints command-specific detail.


def _format_repo_status(result: dict[str, Any]) -> None:
    repo = result.get("repo", {})
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

    if "open_prs" in repo and repo["open_prs"]:
        click.echo(f"  Open PRs: {len(repo['open_prs'])}")
        for pr in repo["open_prs"]:
            click.echo(f"    #{pr.get('number', '?')}: {pr.get('title', '')}")


def _format_workspace_status(result: dict[str, Any]) -> None:
    workspace = result.get("workspace", {})
    repos = result.get("repos", [])

    click.echo(f"  Workspace: {workspace.get('name', 'unknown')}")
    click.echo(f"  Repositories: {len(repos)}")

    for repo in repos:
        name = repo.get("name", "unknown")
        if not repo.get("present"):
            click.echo(f"    {click.style(name, fg='red')} (missing)")
            continue

        branch = repo.get("branch", "?")
        icon = click.style("*", fg="yellow") if repo.get("dirty") else click.style("ok", fg="green")
        click.echo(f"    [{icon}] {name} ({branch})")


def _format_issues(result: dict[str, Any]) -> None:
    issues = result.get("issues", [])
    if not issues:
        click.echo("  No issues found")
        return
    click.echo(f"  Found {len(issues)} issues:")
    for issue in issues:
        labels = issue.get("labels", [])
        label_str = f" [{', '.join(labels)}]" if labels else ""
        click.echo(f"    #{issue.get('number', '?')}: {issue.get('title', '')}{label_str}")


def _format_branch(result: dict[str, Any]) -> None:
    if "branch" in result:
        click.echo(f"  Branch: {click.style(result['branch'], fg='cyan')}")


def _format_foreach(result: dict[str, Any]) -> None:
    results = result.get("results", [])
    for r in results:
        repo = r.get("repo", r.get("name", "unknown"))
        success = r.get("success", False)
        if success:
            click.echo(f"  {click.style('[ok]', fg='green')} {repo}")
        else:
            click.echo(
                f"  {click.style('[error]', fg='red')} {repo} (exit {r.get('exit_code', 0)})"
            )
        if output := r.get("output"):
            for line in output.split("\n"):
                if line.strip():
                    click.echo(f"      {line}")


def _format_check_plan(result: dict[str, Any]) -> None:
    phases = result.get("phases", [])
    if not phases:
        click.echo("  No phases in plan")
        return
    click.echo(f"  Preset: {result.get('preset', 'unknown')}")
    for phase in phases:
        click.echo(f"    {phase['name']}: {phase['command']}")


def _format_check_run(result: dict[str, Any]) -> None:
    phases = result.get("phases", [])
    for phase in phases:
        status = phase.get("status", "unknown")
        icon = (
            click.style("[ok]", fg="green")
            if status == "passed"
            else click.style(f"[{status}]", fg="red")
        )
        duration = phase.get("duration_seconds", 0)
        click.echo(f"    {icon} {phase['phase']} ({duration:.1f}s)")
        for failure in phase.get("failures", []):
            click.echo(f"      {failure}")


def _format_inspect(result: dict[str, Any]) -> None:
    for key in [
        "repo_kind",
        "language",
        "framework",
        "default_branch",
        "current_branch",
        "target_pr_branch",
    ]:
        if key in result:
            click.echo(f"  {key}: {result[key]}")


def _format_audit(result: dict[str, Any]) -> None:
    findings = result.get("findings", [])
    if not findings:
        click.echo("  No findings")
        return
    for f in findings:
        severity = f.get("severity", "info")
        color = "red" if severity == "error" else "yellow" if severity == "warning" else "white"
        click.echo(f"  {click.style(f'[{severity}]', fg=color)} {f['id']}: {f.get('subject', '')}")
        click.echo(f"    expected: {f.get('expected', '')}")
        click.echo(f"    actual: {f.get('actual', '')}")


# Command -> formatter registry
_HUMAN_FORMATTERS: dict[str, Any] = {
    "repo status": _format_repo_status,
    "repo inspect": _format_inspect,
    "repo issues pick": _format_issues,
    "repo issues view": _format_issues,
    "repo branch prepare": _format_branch,
    "repo check plan": _format_check_plan,
    "repo check run": _format_check_run,
    "repo submit": _format_branch,
    "workspace status": _format_workspace_status,
    "workspace inspect": _format_inspect,
    "workspace issues": _format_issues,
    "workspace branch": _format_branch,
    "workspace check": _format_check_run,
    "workspace foreach": _format_foreach,
    "standardize detect": _format_inspect,
    "standardize audit": _format_audit,
}


# --- Convenience helpers ---


def emit_error(message: str, exit_code: int | None = None) -> None:
    """Emit error message to stderr."""
    click.echo(click.style(f"Error: {message}", fg="red"), err=True)
    if exit_code is not None:
        sys.exit(exit_code)


def emit_warning(message: str) -> None:
    """Emit warning message to stderr."""
    click.echo(click.style(f"Warning: {message}", fg="yellow"), err=True)


def emit_stub(command: str, scope: str, *, json_mode: bool = False) -> None:
    """Emit a stub response for unimplemented commands."""
    response = CommandResponse(
        command=command,
        scope=scope,
        status="error",
        summary=f"{command} is not yet implemented",
        errors=["Not yet implemented"],
        result={"implemented": False},
    )
    if not json_mode:
        emit_warning(f"{command} is not yet implemented")
    emit_response(response, json_mode=json_mode)
