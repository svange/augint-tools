"""Top-level project commands."""

import sys
from pathlib import Path

import click

from augint_tools.config import create_ai_shell_config, load_ai_shell_config
from augint_tools.git import detect_base_branch, is_git_repo
from augint_tools.output import CommandResponse, emit_response


@click.command()
@click.option(
    "--library", "repo_type", flag_value="library", default=None, help="Initialize as library repo"
)
@click.option("--service", "repo_type", flag_value="service", help="Initialize as service repo")
@click.option(
    "--workspace", "repo_type", flag_value="workspace", help="Initialize as workspace repo"
)
@click.pass_context
def init(ctx, repo_type: str | None) -> None:
    """Initialize repo or workspace workflow metadata."""
    cwd = Path.cwd()
    config_path = cwd / "ai-shell.toml"
    obj = ctx.obj or {}
    opts = {
        "json_mode": obj.get("json_mode", False),
        "actionable": obj.get("actionable", False),
        "summary_only": obj.get("summary_only", False),
    }

    if not is_git_repo(cwd):
        emit_response(
            CommandResponse.error("init", "repo", "Not in a git repository. Run 'git init' first."),
            **opts,
        )
        sys.exit(1)

    # Load existing config if present
    existing_config = load_ai_shell_config(config_path)

    # Determine repo type
    if repo_type is None:
        if existing_config:
            repo_type = existing_config.repo_type
        else:
            repo_type = click.prompt(
                "Repository type",
                type=click.Choice(["library", "service", "workspace"]),
                default="library",
            )

    # Determine branch strategy
    if repo_type == "library":
        branch_strategy = "main"
    else:
        if existing_config and existing_config.branch_strategy:
            branch_strategy = existing_config.branch_strategy
        else:
            base_branch = detect_base_branch(cwd)
            if base_branch in ["dev", "develop"]:
                branch_strategy = "dev"
            else:
                branch_strategy = "main"

    # Create/update config
    try:
        create_ai_shell_config(
            config_path,
            repo_type=repo_type,
            branch_strategy=branch_strategy,
        )
    except Exception as e:
        emit_response(
            CommandResponse.error("init", "repo", f"Failed to create config: {e}"),
            **opts,
        )
        sys.exit(1)

    emit_response(
        CommandResponse.ok(
            "init",
            "repo",
            f"Initialized {repo_type} repo with {branch_strategy} strategy",
            result={
                "repo_type": repo_type,
                "branch_strategy": branch_strategy,
                "config_file": str(config_path),
            },
        ),
        **opts,
    )
