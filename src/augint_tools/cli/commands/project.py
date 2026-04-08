"""Top-level project commands."""

from pathlib import Path

import click

from augint_tools.config import create_ai_shell_config, load_ai_shell_config
from augint_tools.git import detect_base_branch, is_git_repo
from augint_tools.output import emit_error, emit_output


@click.command()
@click.option(
    "--library", "repo_type", flag_value="library", default=None, help="Initialize as library repo"
)
@click.option("--service", "repo_type", flag_value="service", help="Initialize as service repo")
@click.option(
    "--workspace", "repo_type", flag_value="workspace", help="Initialize as workspace repo"
)
@click.option("--json", "as_json", is_flag=True, default=False, help="Output JSON")
def init(repo_type: str | None, as_json: bool) -> None:
    """Initialize repo or workspace workflow metadata."""
    cwd = Path.cwd()
    config_path = cwd / "ai-shell.toml"

    # Check if we're in a git repo
    if not is_git_repo(cwd):
        emit_error("Not in a git repository. Run 'git init' first.", exit_code=1)
        return

    # Load existing config if present
    existing_config = load_ai_shell_config(config_path)

    # Determine repo type
    if repo_type is None:
        if existing_config:
            repo_type = existing_config.repo_type
        else:
            # Interactive prompt
            repo_type = click.prompt(
                "Repository type",
                type=click.Choice(["library", "service", "workspace"]),
                default="library",
            )

    # Determine branch strategy
    if repo_type == "library":
        branch_strategy = "main"
    else:
        # For service/workspace, detect or use existing
        if existing_config and existing_config.branch_strategy:
            branch_strategy = existing_config.branch_strategy
        else:
            # Auto-detect from repository
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
        emit_error(f"Failed to create config: {e}", exit_code=1)
        return

    # Output result
    emit_output(
        command="init",
        scope="repo",
        as_json=as_json,
        status="ok",
        repo_type=repo_type,
        branch_strategy=branch_strategy,
        config_file=str(config_path),
    )

    if not as_json:
        click.echo(f"Created {config_path}")
