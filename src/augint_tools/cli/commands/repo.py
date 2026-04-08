"""Single repository workflow commands."""

from pathlib import Path

import click

from augint_tools.config import load_ai_shell_config
from augint_tools.execution import run_command
from augint_tools.execution.runner import discover_lint_command, discover_test_command
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
def repo() -> None:
    """Single repository workflow commands."""
    pass


@repo.command()
@click.option("--json", "as_json", is_flag=True, default=False, help="Output JSON")
def status(as_json: bool) -> None:
    """Show repository status."""
    cwd = Path.cwd()

    # Check if in git repo
    if not is_git_repo(cwd):
        if as_json:
            emit_json(create_error_response("status", "repo", "Not in a git repository"))
        else:
            emit_error("Not in a git repository", exit_code=1)
        return

    # Get git status
    repo_status = get_repo_status(cwd)

    # Build repo info
    repo_info = {
        "path": str(cwd),
        "branch": repo_status.branch,
        "dirty": repo_status.dirty,
        "dirty_files": repo_status.dirty_files,
        "ahead": repo_status.ahead,
        "behind": repo_status.behind,
    }

    # Load config to get base branch
    config = load_ai_shell_config(cwd / "ai-shell.toml")
    if config:
        repo_info["base_branch"] = config.base_branch
        repo_info["pr_target"] = config.pr_target_branch

    # Get GitHub info if available
    github_info = {
        "available": False,
        "authenticated": False,
        "open_prs": [],
    }

    if is_gh_available():
        github_info["available"] = True
        if is_gh_authenticated():
            github_info["authenticated"] = True
            # Get open PRs for current branch
            if repo_status.branch:
                prs = get_open_prs(branch=repo_status.branch)
                github_info["open_prs"] = [
                    {
                        "number": pr.number,
                        "title": pr.title,
                        "url": pr.url,
                        "state": pr.state,
                    }
                    for pr in prs
                ]

    emit_output(
        command="status",
        scope="repo",
        as_json=as_json,
        repo=repo_info,
        github=github_info,
    )


@repo.command()
@click.argument("query", required=False)
@click.option("--json", "as_json", is_flag=True, default=False, help="Output JSON")
def issues(query: str | None, as_json: bool) -> None:
    """List issues for this repository."""
    cwd = Path.cwd()

    # Check if in git repo
    if not is_git_repo(cwd):
        if as_json:
            emit_json(create_error_response("issues", "repo", "Not in a git repository"))
        else:
            emit_error("Not in a git repository", exit_code=1)
        return

    # Check GitHub CLI
    if not is_gh_available():
        if as_json:
            emit_json(create_error_response("issues", "repo", "GitHub CLI (gh) not available"))
        else:
            emit_error("GitHub CLI (gh) not available. Install with: apt install gh", exit_code=1)
        return

    if not is_gh_authenticated():
        if as_json:
            emit_json(create_error_response("issues", "repo", "GitHub CLI not authenticated"))
        else:
            emit_error("Run 'gh auth login' to authenticate", exit_code=1)
        return

    # List issues
    issue_list = list_issues(query=query)

    issues_data = [
        {
            "number": issue.number,
            "title": issue.title,
            "state": issue.state,
            "labels": issue.labels,
            "url": issue.url,
        }
        for issue in issue_list
    ]

    emit_output(
        command="issues",
        scope="repo",
        as_json=as_json,
        query=query,
        issues=issues_data,
        count=len(issues_data),
    )


@repo.command()
@click.argument("branch_name")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output JSON")
def branch(branch_name: str, as_json: bool) -> None:
    """Create or switch to branch."""
    cwd = Path.cwd()

    # Check if in git repo
    if not is_git_repo(cwd):
        if as_json:
            emit_json(create_error_response("branch", "repo", "Not in a git repository"))
        else:
            emit_error("Not in a git repository", exit_code=1)
        return

    # Check if branch exists
    if branch_exists(cwd, branch_name):
        # Switch to existing branch
        if switch_branch(cwd, branch_name):
            emit_output(
                command="branch",
                scope="repo",
                as_json=as_json,
                branch=branch_name,
                created=False,
            )
        else:
            if as_json:
                emit_json(
                    create_error_response("branch", "repo", f"Failed to switch to {branch_name}")
                )
            else:
                emit_error(f"Failed to switch to {branch_name}", exit_code=1)
        return

    # Get base branch from config or detect
    config = load_ai_shell_config(cwd / "ai-shell.toml")
    if config:
        base_branch = config.base_branch
    else:
        from augint_tools.git.repo import detect_base_branch

        base_branch = detect_base_branch(cwd)
        emit_warning(f"No ai-shell.toml found, using detected base branch: {base_branch}")

    # Create new branch
    if create_branch(cwd, branch_name, base_branch):
        emit_output(
            command="branch",
            scope="repo",
            as_json=as_json,
            branch=branch_name,
            base=base_branch,
            created=True,
        )
    else:
        if as_json:
            emit_json(
                create_error_response("branch", "repo", f"Failed to create branch {branch_name}")
            )
        else:
            emit_error(f"Failed to create branch {branch_name}", exit_code=1)


@repo.command()
@click.option("--json", "as_json", is_flag=True, default=False, help="Output JSON")
def test(as_json: bool) -> None:
    """Run tests for this repository."""
    cwd = Path.cwd()

    # Check if in git repo
    if not is_git_repo(cwd):
        if as_json:
            emit_json(create_error_response("test", "repo", "Not in a git repository"))
        else:
            emit_error("Not in a git repository", exit_code=1)
        return

    # Discover test command
    test_cmd = discover_test_command(cwd)
    if not test_cmd:
        if as_json:
            emit_json(create_error_response("test", "repo", "No test command found"))
        else:
            emit_error(
                "No test command found. Configure in ai-shell.toml or use standard conventions.",
                exit_code=1,
            )
        return

    # Run tests
    if not as_json:
        click.echo(f"Running: {test_cmd}")

    result = run_command(test_cmd, cwd=cwd)

    if as_json:
        emit_output(
            command="test",
            scope="repo",
            as_json=True,
            status="ok" if result.success else "error",
            test_command=test_cmd,
            exit_code=result.exit_code,
            success=result.success,
        )
    else:
        # Show output
        if result.stdout:
            click.echo(result.stdout)
        if result.stderr:
            click.echo(result.stderr, err=True)

        if result.success:
            emit_output("test", "repo", as_json=False)
        else:
            emit_error(
                f"Tests failed with exit code {result.exit_code}", exit_code=result.exit_code
            )


@repo.command()
@click.option("--fix", is_flag=True, default=False, help="Automatically fix issues")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output JSON")
def lint(fix: bool, as_json: bool) -> None:
    """Run lint and quality checks."""
    cwd = Path.cwd()

    # Check if in git repo
    if not is_git_repo(cwd):
        if as_json:
            emit_json(create_error_response("lint", "repo", "Not in a git repository"))
        else:
            emit_error("Not in a git repository", exit_code=1)
        return

    # Discover lint command
    lint_cmd = discover_lint_command(cwd)
    if not lint_cmd:
        if as_json:
            emit_json(create_error_response("lint", "repo", "No lint command found"))
        else:
            emit_error(
                "No lint command found. Configure in ai-shell.toml or use standard conventions.",
                exit_code=1,
            )
        return

    # Add --fix if supported and requested
    if fix and "ruff" in lint_cmd:
        lint_cmd = lint_cmd.replace("ruff check", "ruff check --fix")
    elif fix and "pre-commit" in lint_cmd:
        # pre-commit auto-fixes by default
        pass

    # Run lint
    if not as_json:
        click.echo(f"Running: {lint_cmd}")

    result = run_command(lint_cmd, cwd=cwd)

    if as_json:
        emit_output(
            command="lint",
            scope="repo",
            as_json=True,
            status="ok" if result.success else "error",
            lint_command=lint_cmd,
            exit_code=result.exit_code,
            success=result.success,
            fix=fix,
        )
    else:
        # Show output
        if result.stdout:
            click.echo(result.stdout)
        if result.stderr:
            click.echo(result.stderr, err=True)

        if result.success:
            emit_output("lint", "repo", as_json=False)
        else:
            emit_error(f"Lint failed with exit code {result.exit_code}", exit_code=result.exit_code)


@repo.command()
@click.option("--json", "as_json", is_flag=True, default=False, help="Output JSON")
def submit(as_json: bool) -> None:
    """Push branch and create PR."""
    cwd = Path.cwd()

    # Check if in git repo
    if not is_git_repo(cwd):
        if as_json:
            emit_json(create_error_response("submit", "repo", "Not in a git repository"))
        else:
            emit_error("Not in a git repository", exit_code=1)
        return

    # Check GitHub CLI
    if not is_gh_available():
        if as_json:
            emit_json(create_error_response("submit", "repo", "GitHub CLI (gh) not available"))
        else:
            emit_error("GitHub CLI (gh) not available. Install with: apt install gh", exit_code=1)
        return

    if not is_gh_authenticated():
        if as_json:
            emit_json(create_error_response("submit", "repo", "GitHub CLI not authenticated"))
        else:
            emit_error("Run 'gh auth login' to authenticate", exit_code=1)
        return

    # Get current branch
    current_branch = get_current_branch(cwd)
    if not current_branch:
        if as_json:
            emit_json(create_error_response("submit", "repo", "Not on a branch (detached HEAD)"))
        else:
            emit_error("Not on a branch (detached HEAD)", exit_code=1)
        return

    # Get config for target branch
    config = load_ai_shell_config(cwd / "ai-shell.toml")
    if config:
        target_branch = config.pr_target_branch
    else:
        from augint_tools.git.repo import detect_base_branch

        target_branch = detect_base_branch(cwd)
        emit_warning(f"No ai-shell.toml found, using detected target: {target_branch}")

    # Check if already on main/dev
    if current_branch in ["main", "master", "dev", "develop", target_branch]:
        if as_json:
            emit_json(
                create_error_response("submit", "repo", f"Cannot submit from {current_branch}")
            )
        else:
            emit_error(
                f"Cannot submit from {current_branch}. Create a feature branch first.", exit_code=1
            )
        return

    # Push branch
    if not as_json:
        click.echo(f"Pushing {current_branch}...")

    if not push_branch(cwd, current_branch, set_upstream=True):
        if as_json:
            emit_json(create_error_response("submit", "repo", "Failed to push branch"))
        else:
            emit_error("Failed to push branch", exit_code=1)
        return

    # Check if PR already exists
    existing_prs = get_open_prs(branch=current_branch)
    if existing_prs:
        pr = existing_prs[0]
        emit_output(
            command="submit",
            scope="repo",
            as_json=as_json,
            branch=current_branch,
            target=target_branch,
            pr_url=pr.url,
            pr_number=pr.number,
            pr_exists=True,
        )
        if not as_json:
            click.echo(f"PR already exists: {pr.url}")
        return

    # Create PR
    pr_title = current_branch.replace("-", " ").replace("_", " ").title()
    pr_body = f"Pull request for {current_branch}"

    if not as_json:
        click.echo(f"Creating PR: {pr_title}")

    pr_url = create_pr(title=pr_title, base=target_branch, body=pr_body)
    if not pr_url:
        if as_json:
            emit_json(create_error_response("submit", "repo", "Failed to create PR"))
        else:
            emit_error("Failed to create PR", exit_code=1)
        return

    # Extract PR number from URL
    pr_number = int(pr_url.split("/")[-1])

    # Enable automerge
    automerge_enabled = enable_automerge(pr_number)

    emit_output(
        command="submit",
        scope="repo",
        as_json=as_json,
        branch=current_branch,
        target=target_branch,
        pr_url=pr_url,
        pr_number=pr_number,
        automerge_enabled=automerge_enabled,
        pr_exists=False,
    )

    if not as_json:
        click.echo(f"PR created: {pr_url}")
        if automerge_enabled:
            click.echo("Auto-merge enabled")
        else:
            emit_warning("Could not enable auto-merge")
