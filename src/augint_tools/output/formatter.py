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


def _format_ide_info(result: dict[str, Any]) -> None:
    click.echo(f"  Project : {result.get('project_name', '?')}")
    click.echo(f"  Venv    : {result.get('venv_path', '?')}")
    click.echo(f"  Python  : {result.get('python_version', '?')}")
    click.echo(f"  SDK name: {result.get('sdk_name', '?')}")
    iml = result.get("iml_path")
    click.echo(f"  IML file: {iml or click.style('(none)', fg='yellow')}")
    click.echo(
        f"  .idea/  : {'exists' if result.get('idea_dir_exists') else click.style('missing', fg='yellow')}"
    )
    win_proj = result.get("windows_project_dir")
    if win_proj:
        click.echo(f"  Win path: {win_proj}")
    jb = result.get("jb_options_dir")
    if jb:
        click.echo(f"  JB cfg  : {jb}")
    gh = result.get("gh_token_present")
    click.echo(f"  GH_TOKEN: {'set' if gh else click.style('not set', fg='yellow')}")


def _format_ide_setup(result: dict[str, Any]) -> None:
    # Step details were already printed line-by-line during execution.
    # Only show the SDK name hint for quick reference.
    sdk = result.get("sdk_name")
    if sdk:
        click.echo(f"  SDK name to use in IDEA: {click.style(sdk, bold=True)}")


def _format_env_classify(result: dict[str, Any]) -> None:
    secrets = result.get("secrets", [])
    variables = result.get("variables", [])
    skipped = result.get("skipped", [])

    if secrets:
        click.echo(f"  {click.style('Secrets:', bold=True)}")
        for s in secrets:
            reasons = ", ".join(s.get("reasons", []))
            click.echo(f"    {click.style(s['key'], fg='red')} ({reasons})")
    if variables:
        click.echo(f"  {click.style('Variables:', bold=True)}")
        for v in variables:
            click.echo(f"    {click.style(v, fg='cyan')}")
    if skipped:
        click.echo(f"  {click.style('Skipped:', bold=True)}")
        for s in skipped:
            click.echo(f"    {click.style(s, fg='yellow')}")


# Command -> formatter registry
_HUMAN_FORMATTERS: dict[str, Any] = {
    "workspace status": _format_workspace_status,
    "workspace branch": _format_branch,
    "workspace check": _format_check_run,
    "workspace foreach": _format_foreach,
    "ide info": _format_ide_info,
    "ide setup": _format_ide_setup,
    "env classify": _format_env_classify,
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
