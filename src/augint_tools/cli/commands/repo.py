"""Single repository workflow commands."""

import sys
from pathlib import Path

import click

from augint_tools.checks import resolve_plan, run_plan
from augint_tools.detection import detect
from augint_tools.git import (
    branch_exists,
    create_branch,
    get_current_branch,
    get_repo_status,
    is_git_repo,
    push_branch,
    switch_branch,
)
from augint_tools.github import (
    create_pr,
    enable_automerge,
    get_open_prs,
)
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


# --- Top-level group ---


@click.group()
@click.pass_context
def repo(ctx):
    """Single repository workflow commands."""
    ctx.ensure_object(dict)


# --- repo status ---


@repo.command()
@click.pass_context
def status(ctx):
    """Summarize git state, upstream, open PR, latest CI run, and next action."""
    cwd = _require_git(ctx, "repo status")
    context = detect(cwd)
    repo_status = get_repo_status(cwd)

    repo_info = {
        "path": str(cwd),
        "branch": repo_status.branch,
        "dirty": repo_status.dirty,
        "dirty_files": repo_status.dirty_files,
        "ahead": repo_status.ahead,
        "behind": repo_status.behind,
        "base_branch": context.target_pr_branch,
    }

    # GitHub info
    open_prs = []
    ci_run = None
    if context.github.authenticated and repo_status.branch:
        prs = get_open_prs(branch=repo_status.branch)
        open_prs = [
            {"number": pr.number, "title": pr.title, "url": pr.url, "state": pr.state} for pr in prs
        ]
        repo_info["open_prs"] = open_prs

        # Latest CI run
        try:
            result = run_gh(
                [
                    "run",
                    "list",
                    "--branch",
                    repo_status.branch,
                    "--limit",
                    "1",
                    "--json",
                    "status,conclusion,name,url",
                ],
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                import json

                runs = json.loads(result.stdout)
                if runs:
                    ci_run = runs[0]
                    repo_info["ci_run"] = ci_run
        except Exception:
            pass

    # Compute next action
    next_actions = _compute_next_actions(repo_status, context, open_prs, ci_run)

    # Summary
    parts = []
    if repo_status.branch:
        parts.append(f"on {repo_status.branch}")
    if repo_status.dirty:
        parts.append(f"{len(repo_status.dirty_files)} dirty files")
    if repo_status.ahead > 0:
        parts.append(f"{repo_status.ahead} ahead")
    if open_prs:
        parts.append(f"{len(open_prs)} open PRs")
    summary = ", ".join(parts) if parts else "clean"

    emit_response(
        CommandResponse(
            command="repo status",
            scope="repo",
            status="ok",
            summary=summary,
            next_actions=next_actions,
            result={
                "repo": repo_info,
                "github": {
                    "available": context.github.available,
                    "authenticated": context.github.authenticated,
                },
            },
        ),
        **_get_output_opts(ctx),
    )


def _compute_next_actions(repo_status, context, open_prs, ci_run) -> list[str]:
    """Compute recommended next actions based on current state."""
    actions = []
    if repo_status.dirty:
        actions.append("commit or stash changes")
    if repo_status.ahead > 0 and not open_prs:
        actions.append("push and open PR")
    if repo_status.behind > 0:
        actions.append("pull latest changes")
    if open_prs and ci_run:
        conclusion = ci_run.get("conclusion", "")
        if conclusion == "failure":
            actions.append("triage CI failures")
        elif conclusion == "" or ci_run.get("status") == "in_progress":
            actions.append("monitor CI")
    if not repo_status.dirty and repo_status.ahead == 0 and not open_prs:
        actions.append("pick an issue")
    return actions


# --- repo branch ---


@repo.group()
def branch():
    """Branch management."""
    pass


@branch.command()
@click.option("--issue", type=int, help="Issue number to derive branch name from.")
@click.option("--description", help="Short description for branch name.")
@click.option("--name", help="Exact branch name to use.")
@click.pass_context
def prepare(ctx, issue, description, name):
    """Create or switch to the correct work branch from the correct base."""
    cwd = _require_git(ctx, "repo branch prepare")
    context = detect(cwd)
    opts = _get_output_opts(ctx)

    # Resolve branch name
    if name:
        branch_name = name
    elif issue:
        branch_name = f"feat/issue-{issue}"
        if description:
            slug = description.lower().replace(" ", "-")[:40]
            branch_name = f"feat/issue-{issue}-{slug}"
    elif description:
        slug = description.lower().replace(" ", "-")[:50]
        branch_name = f"feat/{slug}"
    else:
        emit_response(
            CommandResponse.error(
                "repo branch prepare", "repo", "Provide --issue, --description, or --name"
            ),
            **opts,
        )
        sys.exit(1)

    base = context.target_pr_branch

    # Check if branch already exists
    if branch_exists(cwd, branch_name):
        if switch_branch(cwd, branch_name):
            emit_response(
                CommandResponse.ok(
                    "repo branch prepare",
                    "repo",
                    f"Switched to existing branch {branch_name}",
                    result={"branch": branch_name, "base": base, "created": False},
                ),
                **opts,
            )
        else:
            emit_response(
                CommandResponse.error(
                    "repo branch prepare", "repo", f"Failed to switch to {branch_name}"
                ),
                **opts,
            )
            sys.exit(1)
        return

    # Create new branch from base
    if create_branch(cwd, branch_name, base):
        # Push and set upstream
        pushed = push_branch(cwd, branch_name, set_upstream=True)
        emit_response(
            CommandResponse.ok(
                "repo branch prepare",
                "repo",
                f"Created branch {branch_name} from {base}",
                result={"branch": branch_name, "base": base, "created": True, "pushed": pushed},
                next_actions=["start development"],
            ),
            **opts,
        )
    else:
        emit_response(
            CommandResponse.error(
                "repo branch prepare", "repo", f"Failed to create branch {branch_name}"
            ),
            **opts,
        )
        sys.exit(1)


# --- repo submit ---


@repo.command()
@click.option("--preset", default="default", type=click.Choice(["quick", "default", "full", "ci"]))
@click.option("--skip", help="Comma-separated check phases to skip.")
@click.option("--draft", is_flag=True, default=False, help="Create PR as draft.")
@click.pass_context
def submit(ctx, preset, skip, draft):
    """Push branch and create PR with checks."""
    cwd = _require_git(ctx, "repo submit")
    context = detect(cwd)
    opts = _get_output_opts(ctx)

    if not context.github.authenticated:
        emit_response(
            CommandResponse.error("repo submit", "repo", "GitHub CLI not authenticated"), **opts
        )
        sys.exit(1)

    current = get_current_branch(cwd)
    if not current:
        emit_response(
            CommandResponse.error("repo submit", "repo", "Not on a branch (detached HEAD)"), **opts
        )
        sys.exit(1)

    target = context.target_pr_branch
    if current in ["main", "master", "dev", "develop", target]:
        emit_response(
            CommandResponse.error(
                "repo submit",
                "repo",
                f"Cannot submit from {current}. Create a feature branch first.",
            ),
            **opts,
        )
        sys.exit(1)

    # Run checks
    skip_list = [s.strip() for s in skip.split(",")] if skip else None
    plan = resolve_plan(context.command_plan, preset=preset, skip=skip_list)
    if plan.phases:
        results = run_plan(plan, cwd)
        failed = [r for r in results if r.status == "failed"]
        if failed:
            phase_names = ", ".join(r.phase for r in failed)
            emit_response(
                CommandResponse(
                    command="repo submit",
                    scope="repo",
                    status="error",
                    summary=f"Checks failed: {phase_names}",
                    errors=[f"{r.phase} failed (exit {r.exit_code})" for r in failed],
                    result={"phases": [r.to_dict() for r in results]},
                    next_actions=["fix failures and retry"],
                ),
                **opts,
            )
            sys.exit(1)

    # Push branch
    if not push_branch(cwd, current, set_upstream=True):
        emit_response(CommandResponse.error("repo submit", "repo", "Failed to push branch"), **opts)
        sys.exit(1)

    # Check for existing PR
    existing_prs = get_open_prs(branch=current)
    if existing_prs:
        pr = existing_prs[0]
        emit_response(
            CommandResponse.ok(
                "repo submit",
                "repo",
                f"PR already exists: #{pr.number}",
                result={
                    "branch": current,
                    "target": target,
                    "pr_url": pr.url,
                    "pr_number": pr.number,
                    "pr_exists": True,
                },
                next_actions=["monitor ci"],
            ),
            **opts,
        )
        return

    # Create PR
    pr_title = current.replace("-", " ").replace("_", " ").title()
    pr_body = f"Pull request for {current}"
    pr_url = create_pr(title=pr_title, base=target, body=pr_body)
    if not pr_url:
        emit_response(CommandResponse.error("repo submit", "repo", "Failed to create PR"), **opts)
        sys.exit(1)

    pr_number = int(pr_url.split("/")[-1])
    automerge = enable_automerge(pr_number) if not draft else False

    emit_response(
        CommandResponse.ok(
            "repo submit",
            "repo",
            f"Created PR #{pr_number}" + (" with automerge" if automerge else ""),
            result={
                "branch": current,
                "target": target,
                "pr_url": pr_url,
                "pr_number": pr_number,
                "automerge_enabled": automerge,
                "draft": draft,
            },
            next_actions=["monitor ci"],
        ),
        **opts,
    )


# --- repo ci ---


@repo.group()
def ci():
    """CI pipeline commands."""
    pass


@ci.command()
@click.option(
    "--fix", "fix_mechanical", is_flag=True, default=False, help="Attempt mechanical fixes."
)
@click.option("--run-id", help="Specific run ID to triage.")
@click.pass_context
def triage(ctx, fix_mechanical, run_id):
    """Classify CI failures and optionally apply mechanical fixes."""
    cwd = _require_git(ctx, "repo ci triage")
    context = detect(cwd)
    opts = _get_output_opts(ctx)

    if not context.github.authenticated:
        emit_response(
            CommandResponse.error("repo ci triage", "repo", "GitHub CLI not authenticated"), **opts
        )
        sys.exit(1)

    branch = context.current_branch
    if not branch and not run_id:
        emit_response(
            CommandResponse.error(
                "repo ci triage", "repo", "Not on a branch and no --run-id provided"
            ),
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
                CommandResponse.error(
                    "repo ci triage", "repo", f"gh failed: {result.stderr.strip()}"
                ),
                **opts,
            )
            sys.exit(1)

        import json as json_mod

        data = json_mod.loads(result.stdout)

        if isinstance(data, list):
            if not data:
                emit_response(
                    CommandResponse.ok(
                        "repo ci triage", "repo", "No failed runs found", result={"failures": []}
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
                command="repo ci triage",
                scope="repo",
                status="action-required" if failures else "ok",
                summary=summary,
                result={"failures": failures, "run_id": rid},
                next_actions=next_actions,
            ),
            **opts,
        )
    except Exception as e:
        emit_response(CommandResponse.error("repo ci triage", "repo", str(e)), **opts)
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
