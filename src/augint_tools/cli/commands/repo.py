"""Single repository workflow commands."""

import sys
from pathlib import Path

import click

from augint_tools.detection import detect
from augint_tools.git import is_git_repo
from augint_tools.github.cli import run_gh
from augint_tools.output import CommandResponse, emit_response


def _get_output_opts(ctx: click.Context) -> dict:
    """Extract global output options from Click context."""
    obj = ctx.obj or {}
    return {
        "json_mode": obj.get("json_mode", False),
        "actionable": obj.get("actionable", False),
        "summary_only": obj.get("summary_only", False),
    }


def _require_git(ctx: click.Context, command: str) -> Path | None:
    """Check we're in a git repo, emit error if not. Returns cwd or None."""
    cwd = Path.cwd()
    if not is_git_repo(cwd):
        emit_response(
            CommandResponse.error(command, "repo", "Not in a git repository"),
            **_get_output_opts(ctx),
        )
        sys.exit(1)
    return cwd


@click.command()
@click.option(
    "--fix", "fix_mechanical", is_flag=True, default=False, help="Attempt mechanical fixes."
)
@click.option("--run-id", help="Specific run ID to triage.")
@click.pass_context
def triage(ctx, fix_mechanical, run_id):
    """Classify CI failures and optionally apply mechanical fixes."""
    cwd = _require_git(ctx, "triage")
    context = detect(cwd)
    opts = _get_output_opts(ctx)

    if not context.github.authenticated:
        emit_response(
            CommandResponse.error("triage", "repo", "GitHub CLI not authenticated"), **opts
        )
        sys.exit(1)

    branch = context.current_branch
    if not branch and not run_id:
        emit_response(
            CommandResponse.error("triage", "repo", "Not on a branch and no --run-id provided"),
            **opts,
        )
        sys.exit(1)

    # Get failed run
    if run_id:
        args = ["run", "view", run_id, "--json", "databaseId,status,conclusion,jobs"]
    else:
        args = [
            "run",
            "list",
            "--branch",
            branch,
            "--limit",
            "1",
            "--status",
            "failure",
            "--json",
            "databaseId,status,conclusion",
        ]

    try:
        result = run_gh(args, check=False)
        if result.returncode != 0:
            emit_response(
                CommandResponse.error("triage", "repo", f"gh failed: {result.stderr.strip()}"),
                **opts,
            )
            sys.exit(1)

        import json as json_mod

        data = json_mod.loads(result.stdout)

        if isinstance(data, list):
            if not data:
                emit_response(
                    CommandResponse.ok(
                        "triage", "repo", "No failed runs found", result={"failures": []}
                    ),
                    **opts,
                )
                return
            run_data = data[0]
        else:
            run_data = data

        # Get failed job logs
        rid = run_data.get("databaseId", run_id)
        log_result = run_gh(["run", "view", str(rid), "--log-failed"], check=False)
        log_lines = log_result.stdout.strip().split("\n") if log_result.returncode == 0 else []

        # Classify failures
        failures = _classify_failures(log_lines)

        mechanical = [f for f in failures if f["fixability"] == "mechanical"]
        manual = [f for f in failures if f["fixability"] == "manual"]
        external = [f for f in failures if f["fixability"] == "external"]

        summary = f"{len(mechanical)} mechanical, {len(manual)} manual, {len(external)} external"

        next_actions = []
        if mechanical and fix_mechanical:
            next_actions.append("apply mechanical fixes")
        elif mechanical:
            next_actions.append("run with --fix to apply mechanical fixes")
        if manual:
            next_actions.append("fix manual issues")

        emit_response(
            CommandResponse(
                command="triage",
                scope="repo",
                status="action-required" if failures else "ok",
                summary=summary,
                result={"failures": failures, "run_id": rid},
                next_actions=next_actions,
            ),
            **opts,
        )
    except Exception as e:
        emit_response(CommandResponse.error("triage", "repo", str(e)), **opts)
        sys.exit(1)


def _classify_failures(log_lines: list[str]) -> list[dict]:
    """Classify CI log failures into mechanical, manual, or external."""
    failures = []
    current_job = "unknown"

    for line in log_lines:
        # Detect job names from log format "jobname\tstepname\tline"
        parts = line.split("\t")
        if len(parts) >= 2:
            current_job = parts[0]

        lower = line.lower()
        if any(kw in lower for kw in ["error", "failed", "failure", "exception"]):
            fixability = "manual"
            if any(
                kw in lower for kw in ["format", "whitespace", "import sort", "trailing", "lint"]
            ):
                fixability = "mechanical"
            elif any(kw in lower for kw in ["timeout", "rate limit", "network", "connection"]):
                fixability = "external"

            failures.append(
                {
                    "job": current_job,
                    "line": line.strip()[:200],
                    "fixability": fixability,
                }
            )

    return failures[:30]  # cap
