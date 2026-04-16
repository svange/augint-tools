"""Workspace orchestration commands."""

import sys
from pathlib import Path

import click

from augint_tools.checks import resolve_plan, run_plan
from augint_tools.config import load_workspace_config
from augint_tools.detection import detect
from augint_tools.execution import run_command
from augint_tools.execution.workspace import get_repo_path, resolve_clone_url, topological_sort
from augint_tools.git import (
    create_branch,
    get_current_branch,
    get_repo_status,
    is_git_repo,
    push_branch,
    run_git,
)
from augint_tools.github import (
    create_pr,
    enable_automerge,
    get_open_prs,
    is_gh_authenticated,
    is_gh_available,
)
from augint_tools.output import CommandResponse, emit_response


def _get_output_opts(ctx: click.Context) -> dict:
    obj = ctx.obj or {}
    return {"json_mode": obj.get("json_mode", False)}


def _require_workspace(ctx: click.Context, command: str):
    """Load workspace config, emit error if missing. Returns (cwd, config) or exits."""
    cwd = Path.cwd()
    config = load_workspace_config(cwd / "workspace.yaml")
    if not config:
        emit_response(
            CommandResponse.error(command, "workspace", "No workspace.yaml found"),
            **_get_output_opts(ctx),
        )
        sys.exit(1)
    return cwd, config


# --- Top-level group ---


@click.group()
@click.pass_context
def workspace(ctx):
    """Workspace orchestration commands."""
    ctx.ensure_object(dict)


# --- workspace inspect ---


@workspace.command()
@click.pass_context
def inspect(ctx):
    """One-call workspace snapshot."""
    cwd, config = _require_workspace(ctx, "workspace inspect")
    opts = _get_output_opts(ctx)

    repos_info = []
    for repo_config in config.repos:
        repo_path = get_repo_path(cwd, repo_config)
        present = repo_path.exists() and is_git_repo(repo_path)
        info = {
            "name": repo_config.name,
            "path": str(repo_config.path),
            "present": present,
            "repo_type": repo_config.repo_type,
            "base_branch": repo_config.base_branch,
            "pr_target_branch": repo_config.pr_target_branch,
            "depends_on": repo_config.depends_on,
        }
        if present:
            context = detect(repo_path)
            info["language"] = context.language
            info["framework"] = context.framework
        repos_info.append(info)

    emit_response(
        CommandResponse.ok(
            "workspace inspect",
            "workspace",
            f"Workspace {config.name}: {len(repos_info)} repos",
            result={
                "workspace": {"name": config.name, "repos_dir": config.repos_dir},
                "repos": repos_info,
            },
        ),
        **opts,
    )


# --- workspace sync ---


@workspace.command()
@click.pass_context
def sync(ctx):
    """Clone missing repos and update existing repos."""
    cwd, config = _require_workspace(ctx, "workspace sync")
    opts = _get_output_opts(ctx)

    results = []
    for repo_config in config.repos:
        repo_path = get_repo_path(cwd, repo_config)

        if repo_path.exists() and is_git_repo(repo_path):
            try:
                run_git(["pull"], cwd=repo_path, check=True)
                results.append({"name": repo_config.name, "action": "pulled", "success": True})
            except Exception as e:
                results.append(
                    {
                        "name": repo_config.name,
                        "action": "pull_failed",
                        "success": False,
                        "error": str(e),
                    }
                )
        else:
            repo_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                clone_url = resolve_clone_url(repo_config, cwd)
                run_git(["clone", clone_url, str(repo_path)], check=True)
                results.append({"name": repo_config.name, "action": "cloned", "success": True})
            except Exception as e:
                results.append(
                    {
                        "name": repo_config.name,
                        "action": "clone_failed",
                        "success": False,
                        "error": str(e),
                    }
                )

    ok_count = sum(1 for r in results if r["success"])
    fail_count = len(results) - ok_count
    status = "ok" if fail_count == 0 else "partial" if ok_count > 0 else "error"

    emit_response(
        CommandResponse(
            command="workspace sync",
            scope="workspace",
            status=status,
            summary=f"{ok_count} synced, {fail_count} failed",
            result={"results": results},
        ),
        **opts,
    )


# --- workspace status ---


@workspace.command()
@click.option("--blocked-only", is_flag=True, default=False, help="Show only blocked repos.")
@click.option("--dirty-only", is_flag=True, default=False, help="Show only dirty repos.")
@click.pass_context
def status(ctx, blocked_only, dirty_only):
    """Compact actionable workspace health."""
    cwd, config = _require_workspace(ctx, "workspace status")
    opts = _get_output_opts(ctx)

    repos_status = []
    for repo_config in config.repos:
        repo_path = get_repo_path(cwd, repo_config)
        present = repo_path.exists() and is_git_repo(repo_path)

        if present:
            status_info = get_repo_status(repo_path)
            entry = {
                "name": repo_config.name,
                "path": str(repo_path.relative_to(cwd)),
                "present": True,
                "branch": status_info.branch,
                "dirty": status_info.dirty,
                "ahead": status_info.ahead,
                "behind": status_info.behind,
            }
        else:
            entry = {
                "name": repo_config.name,
                "path": str(repo_config.path),
                "present": False,
            }

        # Apply filters
        if dirty_only and not entry.get("dirty"):
            continue
        if (
            blocked_only
            and entry.get("present")
            and not entry.get("dirty")
            and entry.get("ahead", 0) == 0
        ):
            continue

        repos_status.append(entry)

    present_repos = [r for r in repos_status if r.get("present")]
    dirty_count = sum(1 for r in present_repos if r.get("dirty"))
    ahead_count = sum(
        1 for r in present_repos if isinstance(r.get("ahead"), int) and r["ahead"] > 0
    )

    summary_parts = [f"{len(repos_status)} repos"]
    if dirty_count:
        summary_parts.append(f"{dirty_count} dirty")
    if ahead_count:
        summary_parts.append(f"{ahead_count} ahead")

    emit_response(
        CommandResponse.ok(
            "workspace status",
            "workspace",
            ", ".join(summary_parts),
            result={
                "workspace": {"name": config.name, "path": str(cwd), "repos_dir": config.repos_dir},
                "repos": repos_status,
                "summary": {
                    "total": len(repos_status),
                    "present": len(present_repos),
                    "dirty": dirty_count,
                    "ahead": ahead_count,
                },
            },
        ),
        **opts,
    )


# --- workspace branch ---


@workspace.command()
@click.option("--issue", type=int, help="Issue number to derive branch name from.")
@click.option("--description", help="Short description for branch name.")
@click.option("--name", "branch_name", help="Exact branch name.")
@click.pass_context
def branch(ctx, issue, description, branch_name):
    """Create coordinated branches across child repositories."""
    cwd, config = _require_workspace(ctx, "workspace branch")
    opts = _get_output_opts(ctx)

    # Resolve branch name
    if branch_name:
        name = branch_name
    elif issue:
        name = f"feat/issue-{issue}"
        if description:
            slug = description.lower().replace(" ", "-")[:40]
            name = f"feat/issue-{issue}-{slug}"
    elif description:
        slug = description.lower().replace(" ", "-")[:50]
        name = f"feat/{slug}"
    else:
        emit_response(
            CommandResponse.error(
                "workspace branch", "workspace", "Provide --issue, --description, or --name"
            ),
            **opts,
        )
        sys.exit(1)

    results = []
    for repo_config in config.repos:
        repo_path = get_repo_path(cwd, repo_config)
        if not repo_path.exists() or not is_git_repo(repo_path):
            results.append(
                {"name": repo_config.name, "success": False, "error": "Repository not present"}
            )
            continue

        if create_branch(repo_path, name, repo_config.base_branch):
            results.append(
                {
                    "name": repo_config.name,
                    "success": True,
                    "branch": name,
                    "base": repo_config.base_branch,
                }
            )
        else:
            results.append(
                {"name": repo_config.name, "success": False, "error": "Failed to create branch"}
            )

    ok_count = sum(1 for r in results if r["success"])
    emit_response(
        CommandResponse(
            command="workspace branch",
            scope="workspace",
            status="ok" if ok_count == len(results) else "partial",
            summary=f"Created {name} in {ok_count}/{len(results)} repos",
            result={"branch": name, "results": results},
        ),
        **opts,
    )


# --- workspace check ---


@workspace.command("check")
@click.option("--phase", help="Comma-separated phases to run (e.g., quality,tests).")
@click.option("--preset", default="default", type=click.Choice(["quick", "default", "full", "ci"]))
@click.option("--repos", help="Comma-separated repo names to include.")
@click.option(
    "--fix", "fix_mechanical", is_flag=True, default=False, help="Attempt mechanical fixes."
)
@click.pass_context
def workspace_check(ctx, phase, preset, repos, fix_mechanical):
    """Run grouped validation across repos in dependency order."""
    cwd, config = _require_workspace(ctx, "workspace check")
    opts = _get_output_opts(ctx)

    # Filter repos
    repo_filter = {r.strip() for r in repos.split(",")} if repos else None

    try:
        sorted_repos = topological_sort(config.repos)
    except ValueError as e:
        emit_response(CommandResponse.error("workspace check", "workspace", str(e)), **opts)
        sys.exit(1)

    # Build skip list from --phase (only run specified phases, skip the rest)
    if phase:
        requested = {p.strip() for p in phase.split(",")}
        all_phases = {"quality", "security", "licenses", "tests", "build"}
        skip_list = list(all_phases - requested)
    else:
        skip_list = None

    all_results = []
    total_passed = 0
    total_failed = 0

    for repo_config in sorted_repos:
        if repo_filter and repo_config.name not in repo_filter:
            continue

        repo_path = get_repo_path(cwd, repo_config)
        if not repo_path.exists() or not is_git_repo(repo_path):
            all_results.append(
                {"name": repo_config.name, "status": "skipped", "reason": "not present"}
            )
            continue

        context = detect(repo_path)
        plan = resolve_plan(context.command_plan, preset=preset, skip=skip_list)
        if not plan.phases:
            all_results.append(
                {"name": repo_config.name, "status": "skipped", "reason": "no applicable phases"}
            )
            continue

        results = run_plan(plan, repo_path, fix=fix_mechanical)
        passed = sum(1 for r in results if r.status in ("passed", "fixed"))
        failed = sum(1 for r in results if r.status == "failed")
        total_passed += passed
        total_failed += failed

        all_results.append(
            {
                "name": repo_config.name,
                "status": "ok" if failed == 0 else "error",
                "phases": [r.to_dict() for r in results],
            }
        )

    status = "ok" if total_failed == 0 else "error"
    emit_response(
        CommandResponse(
            command="workspace check",
            scope="workspace",
            status=status,
            summary=f"{total_passed} passed, {total_failed} failed across {len(all_results)} repos",
            result={"repos": all_results},
            next_actions=["fix failures"] if total_failed > 0 else [],
        ),
        **opts,
    )
    if total_failed > 0:
        sys.exit(1)


# --- workspace submit ---


@workspace.command()
@click.option("--monitor", is_flag=True, default=False, help="Start CI monitoring after submit.")
@click.pass_context
def submit(ctx, monitor):
    """Open PRs for changed or selected repos."""
    cwd, config = _require_workspace(ctx, "workspace submit")
    opts = _get_output_opts(ctx)

    if not is_gh_available() or not is_gh_authenticated():
        emit_response(
            CommandResponse.error(
                "workspace submit", "workspace", "GitHub CLI not available or not authenticated"
            ),
            **opts,
        )
        sys.exit(1)

    results = []
    for repo_config in config.repos:
        repo_path = get_repo_path(cwd, repo_config)
        if not repo_path.exists() or not is_git_repo(repo_path):
            results.append(
                {"name": repo_config.name, "success": False, "error": "Repository not present"}
            )
            continue

        current = get_current_branch(repo_path)
        if not current or current in [
            "main",
            "master",
            "dev",
            "develop",
            repo_config.pr_target_branch,
        ]:
            results.append(
                {
                    "name": repo_config.name,
                    "success": False,
                    "error": f"On {current}, not a feature branch",
                }
            )
            continue

        status_info = get_repo_status(repo_path)
        if status_info.ahead == 0 and not status_info.dirty:
            results.append(
                {"name": repo_config.name, "success": False, "error": "No changes to submit"}
            )
            continue

        if not push_branch(repo_path, current, set_upstream=True):
            results.append({"name": repo_config.name, "success": False, "error": "Failed to push"})
            continue

        url_parts = repo_config.url.rstrip(".git").split("/")
        repo_name = f"{url_parts[-2]}/{url_parts[-1]}" if len(url_parts) >= 2 else None

        existing_prs = get_open_prs(branch=current, repo=repo_name)
        if existing_prs:
            pr = existing_prs[0]
            results.append(
                {
                    "name": repo_config.name,
                    "success": True,
                    "pr_url": pr.url,
                    "pr_number": pr.number,
                    "pr_exists": True,
                }
            )
            continue

        pr_title = current.replace("-", " ").replace("_", " ").title()
        pr_url = create_pr(
            title=pr_title,
            base=repo_config.pr_target_branch,
            body=f"PR for {current}",
            repo=repo_name,
        )
        if not pr_url:
            results.append(
                {"name": repo_config.name, "success": False, "error": "Failed to create PR"}
            )
            continue

        pr_number = int(pr_url.split("/")[-1])
        enable_automerge(pr_number, repo=repo_name)
        results.append(
            {
                "name": repo_config.name,
                "success": True,
                "pr_url": pr_url,
                "pr_number": pr_number,
                "pr_exists": False,
            }
        )

    ok_count = sum(1 for r in results if r.get("success"))
    emit_response(
        CommandResponse(
            command="workspace submit",
            scope="workspace",
            status="ok" if ok_count == len(results) else "partial" if ok_count > 0 else "error",
            summary=f"{ok_count}/{len(results)} repos submitted",
            result={"results": results},
            next_actions=["monitor ci"] if monitor else [],
        ),
        **opts,
    )


# --- workspace foreach ---


@workspace.command(context_settings={"ignore_unknown_options": True})
@click.argument("command", nargs=-1, type=click.UNPROCESSED, required=True)
@click.pass_context
def foreach(ctx, command):
    """Run a command across all child repositories."""
    cwd, config = _require_workspace(ctx, "workspace foreach")
    opts = _get_output_opts(ctx)

    cmd_str = " ".join(command)
    results = []

    for repo_config in config.repos:
        repo_path = get_repo_path(cwd, repo_config)
        if not repo_path.exists():
            results.append(
                {"repo": repo_config.name, "success": False, "error": "Repository not present"}
            )
            continue

        result = run_command(cmd_str, cwd=repo_path)
        results.append(
            {
                "repo": repo_config.name,
                "success": result.success,
                "exit_code": result.exit_code,
                "output": result.stdout if result.stdout else result.stderr,
            }
        )

    ok_count = sum(1 for r in results if r.get("success"))
    emit_response(
        CommandResponse(
            command="workspace foreach",
            scope="workspace",
            status="ok" if ok_count == len(results) else "partial" if ok_count > 0 else "error",
            summary=f"{ok_count}/{len(results)} repos succeeded",
            result={"command_run": cmd_str, "results": results},
        ),
        **opts,
    )
