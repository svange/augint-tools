"""Workspace and monorepo orchestration commands."""

from pathlib import Path

import click

from augint_tools.config import load_workspace_config
from augint_tools.execution import run_command
from augint_tools.execution.workspace import get_repo_path, topological_sort
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
    list_issues,
)
from augint_tools.output import (
    create_error_response,
    emit_error,
    emit_json,
    emit_output,
    emit_warning,
)


@click.group()
def monorepo() -> None:
    """Workspace and monorepo orchestration commands."""
    pass


# Alias for monorepo
mono = monorepo


@monorepo.command()
@click.option("--json", "as_json", is_flag=True, default=False, help="Output JSON")
def status(as_json: bool) -> None:
    """Show status across all child repositories."""
    cwd = Path.cwd()
    workspace_config = load_workspace_config(cwd / "workspace.toml")

    if not workspace_config:
        if as_json:
            emit_json(create_error_response("status", "monorepo", "No workspace.toml found"))
        else:
            emit_error(
                "No workspace.toml found. Run 'ai-tools init --workspace' first.", exit_code=1
            )
        return

    # Collect status for each repo
    repos_status = []
    for repo_config in workspace_config.repos:
        repo_path = get_repo_path(cwd, repo_config)
        present = repo_path.exists() and is_git_repo(repo_path)

        if present:
            status_info = get_repo_status(repo_path)
            repos_status.append(
                {
                    "name": repo_config.name,
                    "path": str(repo_path.relative_to(cwd)),
                    "present": True,
                    "branch": status_info.branch,
                    "dirty": status_info.dirty,
                    "ahead": status_info.ahead,
                    "behind": status_info.behind,
                }
            )
        else:
            repos_status.append(
                {
                    "name": repo_config.name,
                    "path": str(
                        repo_path.relative_to(cwd) if repo_path.exists() else repo_config.path
                    ),
                    "present": False,
                }
            )

    # Summary
    summary = {
        "total": len(repos_status),
        "present": sum(1 for r in repos_status if r["present"]),
        "dirty": sum(1 for r in repos_status if r.get("dirty", False)),
        "ahead": sum(1 for r in repos_status if r.get("ahead", 0) > 0),
    }

    emit_output(
        command="status",
        scope="monorepo",
        as_json=as_json,
        workspace={
            "name": workspace_config.name,
            "path": str(cwd),
            "repos_dir": workspace_config.repos_dir,
        },
        repos=repos_status,
        summary=summary,
    )


@monorepo.command()
@click.option("--json", "as_json", is_flag=True, default=False, help="Output JSON")
def sync(as_json: bool) -> None:
    """Clone missing repos and update existing repos."""
    cwd = Path.cwd()
    workspace_config = load_workspace_config(cwd / "workspace.toml")

    if not workspace_config:
        if as_json:
            emit_json(create_error_response("sync", "monorepo", "No workspace.toml found"))
        else:
            emit_error("No workspace.toml found", exit_code=1)
        return

    results = []
    for repo_config in workspace_config.repos:
        repo_path = get_repo_path(cwd, repo_config)

        if repo_path.exists() and is_git_repo(repo_path):
            # Pull existing repo
            if not as_json:
                click.echo(f"Pulling {repo_config.name}...")
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
            # Clone missing repo
            if not as_json:
                click.echo(f"Cloning {repo_config.name}...")
            repo_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                run_git(["clone", repo_config.url, str(repo_path)], check=True)
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

    all_success = all(r["success"] for r in results)
    emit_output(
        command="sync",
        scope="monorepo",
        as_json=as_json,
        status="ok" if all_success else "error",
        results=results,
    )


@monorepo.command()
@click.argument("query", required=False)
@click.option("--json", "as_json", is_flag=True, default=False, help="Output JSON")
def issues(query: str | None, as_json: bool) -> None:
    """Aggregate issues across all child repositories."""
    cwd = Path.cwd()
    workspace_config = load_workspace_config(cwd / "workspace.toml")

    if not workspace_config:
        if as_json:
            emit_json(create_error_response("issues", "monorepo", "No workspace.toml found"))
        else:
            emit_error("No workspace.toml found", exit_code=1)
        return

    # Check GitHub CLI
    if not is_gh_available() or not is_gh_authenticated():
        if as_json:
            emit_json(
                create_error_response(
                    "issues", "monorepo", "GitHub CLI not available or not authenticated"
                )
            )
        else:
            emit_error("Run 'gh auth login' to authenticate", exit_code=1)
        return

    # Collect issues from all repos
    all_issues = []
    for repo_config in workspace_config.repos:
        # Extract owner/repo from URL
        url_parts = repo_config.url.rstrip(".git").split("/")
        if len(url_parts) >= 2:
            repo_name = f"{url_parts[-2]}/{url_parts[-1]}"
            issues = list_issues(repo=repo_name, query=query)
            for issue in issues:
                all_issues.append(
                    {
                        "repo": repo_config.name,
                        "number": issue.number,
                        "title": issue.title,
                        "state": issue.state,
                        "labels": issue.labels,
                        "url": issue.url,
                    }
                )

    emit_output(
        command="issues",
        scope="monorepo",
        as_json=as_json,
        query=query,
        issues=all_issues,
        count=len(all_issues),
    )


@monorepo.command()
@click.argument("branch_name")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output JSON")
def branch(branch_name: str, as_json: bool) -> None:
    """Create coordinated branches across child repositories."""
    cwd = Path.cwd()
    workspace_config = load_workspace_config(cwd / "workspace.toml")

    if not workspace_config:
        if as_json:
            emit_json(create_error_response("branch", "monorepo", "No workspace.toml found"))
        else:
            emit_error("No workspace.toml found", exit_code=1)
        return

    results = []
    for repo_config in workspace_config.repos:
        repo_path = get_repo_path(cwd, repo_config)

        if not repo_path.exists() or not is_git_repo(repo_path):
            results.append(
                {"name": repo_config.name, "success": False, "error": "Repository not present"}
            )
            continue

        # Create branch from base_branch
        if not as_json:
            click.echo(f"Creating branch in {repo_config.name}...")

        if create_branch(repo_path, branch_name, repo_config.base_branch):
            results.append(
                {
                    "name": repo_config.name,
                    "success": True,
                    "branch": branch_name,
                    "base": repo_config.base_branch,
                }
            )
        else:
            results.append(
                {"name": repo_config.name, "success": False, "error": "Failed to create branch"}
            )

    all_success = all(r["success"] for r in results)
    emit_output(
        command="branch",
        scope="monorepo",
        as_json=as_json,
        status="ok" if all_success else "error",
        branch=branch_name,
        results=results,
    )


@monorepo.command()
@click.option("--json", "as_json", is_flag=True, default=False, help="Output JSON")
def test(as_json: bool) -> None:
    """Run tests across all child repositories."""
    cwd = Path.cwd()
    workspace_config = load_workspace_config(cwd / "workspace.toml")

    if not workspace_config:
        if as_json:
            emit_json(create_error_response("test", "monorepo", "No workspace.toml found"))
        else:
            emit_error("No workspace.toml found", exit_code=1)
        return

    # Sort repos by dependency order
    try:
        sorted_repos = topological_sort(workspace_config.repos)
    except ValueError as e:
        if as_json:
            emit_json(create_error_response("test", "monorepo", str(e)))
        else:
            emit_error(str(e), exit_code=1)
        return

    results = []
    for repo_config in sorted_repos:
        repo_path = get_repo_path(cwd, repo_config)

        if not repo_path.exists() or not is_git_repo(repo_path):
            results.append(
                {"name": repo_config.name, "success": False, "error": "Repository not present"}
            )
            continue

        if not repo_config.test:
            results.append(
                {"name": repo_config.name, "success": False, "error": "No test command configured"}
            )
            continue

        if not as_json:
            click.echo(f"Testing {repo_config.name}...")

        result = run_command(repo_config.test, cwd=repo_path)
        results.append(
            {
                "name": repo_config.name,
                "success": result.success,
                "exit_code": result.exit_code,
            }
        )

        # Show output in non-JSON mode
        if not as_json and result.stdout:
            click.echo(result.stdout)

    all_success = all(r["success"] for r in results)
    emit_output(
        command="test",
        scope="monorepo",
        as_json=as_json,
        status="ok" if all_success else "error",
        results=results,
    )


@monorepo.command()
@click.option("--fix", is_flag=True, default=False, help="Automatically fix issues")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output JSON")
def lint(fix: bool, as_json: bool) -> None:
    """Run lint and quality checks across child repositories."""
    cwd = Path.cwd()
    workspace_config = load_workspace_config(cwd / "workspace.toml")

    if not workspace_config:
        if as_json:
            emit_json(create_error_response("lint", "monorepo", "No workspace.toml found"))
        else:
            emit_error("No workspace.toml found", exit_code=1)
        return

    results = []
    for repo_config in workspace_config.repos:
        repo_path = get_repo_path(cwd, repo_config)

        if not repo_path.exists() or not is_git_repo(repo_path):
            results.append(
                {"name": repo_config.name, "success": False, "error": "Repository not present"}
            )
            continue

        if not repo_config.lint:
            results.append(
                {"name": repo_config.name, "success": False, "error": "No lint command configured"}
            )
            continue

        lint_cmd = repo_config.lint
        if fix and "ruff" in lint_cmd:
            lint_cmd = lint_cmd.replace("ruff check", "ruff check --fix")

        if not as_json:
            click.echo(f"Linting {repo_config.name}...")

        result = run_command(lint_cmd, cwd=repo_path)
        results.append(
            {
                "name": repo_config.name,
                "success": result.success,
                "exit_code": result.exit_code,
            }
        )

        # Show output in non-JSON mode
        if not as_json and result.stdout:
            click.echo(result.stdout)

    all_success = all(r["success"] for r in results)
    emit_output(
        command="lint",
        scope="monorepo",
        as_json=as_json,
        status="ok" if all_success else "error",
        fix=fix,
        results=results,
    )


@monorepo.command()
@click.option("--json", "as_json", is_flag=True, default=False, help="Output JSON")
def submit(as_json: bool) -> None:
    """Push branches and open PRs for child repositories."""
    cwd = Path.cwd()
    workspace_config = load_workspace_config(cwd / "workspace.toml")

    if not workspace_config:
        if as_json:
            emit_json(create_error_response("submit", "monorepo", "No workspace.toml found"))
        else:
            emit_error("No workspace.toml found", exit_code=1)
        return

    # Check GitHub CLI
    if not is_gh_available() or not is_gh_authenticated():
        if as_json:
            emit_json(
                create_error_response(
                    "submit", "monorepo", "GitHub CLI not available or not authenticated"
                )
            )
        else:
            emit_error("Run 'gh auth login' to authenticate", exit_code=1)
        return

    results = []
    for repo_config in workspace_config.repos:
        repo_path = get_repo_path(cwd, repo_config)

        if not repo_path.exists() or not is_git_repo(repo_path):
            results.append(
                {"name": repo_config.name, "success": False, "error": "Repository not present"}
            )
            continue

        # Get current branch
        current_branch = get_current_branch(repo_path)
        if not current_branch or current_branch in [
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
                    "error": f"On {current_branch}, not a feature branch",
                }
            )
            continue

        # Check if repo has changes
        status_info = get_repo_status(repo_path)
        if status_info.ahead == 0 and not status_info.dirty:
            results.append(
                {"name": repo_config.name, "success": False, "error": "No changes to submit"}
            )
            continue

        # Push branch
        if not as_json:
            click.echo(f"Pushing {repo_config.name}/{current_branch}...")

        if not push_branch(repo_path, current_branch, set_upstream=True):
            results.append({"name": repo_config.name, "success": False, "error": "Failed to push"})
            continue

        # Check for existing PR
        url_parts = repo_config.url.rstrip(".git").split("/")
        repo_name = f"{url_parts[-2]}/{url_parts[-1]}" if len(url_parts) >= 2 else None

        existing_prs = get_open_prs(branch=current_branch, repo=repo_name)
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

        # Create PR
        pr_title = current_branch.replace("-", " ").replace("_", " ").title()
        pr_body = f"Pull request for {current_branch}"

        pr_url = create_pr(
            title=pr_title, base=repo_config.pr_target_branch, body=pr_body, repo=repo_name
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

    all_success = all(r["success"] for r in results)
    emit_output(
        command="submit",
        scope="monorepo",
        as_json=as_json,
        status="ok" if all_success else "error",
        results=results,
    )


@monorepo.command(context_settings={"ignore_unknown_options": True})
@click.argument("command", nargs=-1, type=click.UNPROCESSED, required=True)
@click.option("--json", "as_json", is_flag=True, default=False, help="Output JSON")
def foreach(command: tuple[str, ...], as_json: bool) -> None:
    """Run a command across all child repositories."""
    cwd = Path.cwd()
    workspace_config = load_workspace_config(cwd / "workspace.toml")

    if not workspace_config:
        if as_json:
            emit_json(create_error_response("foreach", "monorepo", "No workspace.toml found"))
        else:
            emit_error("No workspace.toml found", exit_code=1)
        return

    cmd_str = " ".join(command)
    results = []

    for repo_config in workspace_config.repos:
        repo_path = get_repo_path(cwd, repo_config)

        if not repo_path.exists():
            results.append(
                {"repo": repo_config.name, "success": False, "error": "Repository not present"}
            )
            continue

        if not as_json:
            click.echo(f"\n{repo_config.name}:")

        result = run_command(cmd_str, cwd=repo_path)
        results.append(
            {
                "repo": repo_config.name,
                "success": result.success,
                "exit_code": result.exit_code,
                "output": result.stdout if result.stdout else result.stderr,
            }
        )

        # Show output in non-JSON mode
        if not as_json:
            if result.stdout:
                click.echo(result.stdout)
            if result.stderr:
                click.echo(result.stderr, err=True)

    emit_output(
        command="foreach",
        scope="monorepo",
        as_json=as_json,
        command_run=cmd_str,
        results=results,
    )


@monorepo.command()
@click.option("--json", "as_json", is_flag=True, default=False, help="Output JSON")
def update(as_json: bool) -> None:
    """Update downstream repos after upstream changes."""
    if as_json:
        emit_json(create_error_response("update", "monorepo", "Not yet implemented"))
    else:
        emit_warning("The 'update' command is not yet implemented")
        emit_error("This command will propagate dependency updates downstream", exit_code=1)
