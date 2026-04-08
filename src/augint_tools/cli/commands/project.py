"""Top-level project commands."""

import json
from pathlib import Path

import click
from rich.console import Console

console = Console(stderr=True)


def _emit_stub(command_name: str, **payload: object) -> None:
    payload = {"command": command_name, "implemented": False, **payload}
    console.print(json.dumps(payload, indent=2))
    console.print(
        f"[yellow]{command_name} is scaffolded but not implemented yet. Use augint-tools.md as the implementation spec.[/yellow]"
    )


@click.command()
@click.option("--library", "repo_type", flag_value="library", default=None)
@click.option("--service", "repo_type", flag_value="service")
@click.option("--workspace", "repo_type", flag_value="workspace")
def init(repo_type: str | None) -> None:
    """Initialize repo or workspace workflow metadata."""
    _emit_stub("init", cwd=str(Path.cwd()), repo_type=repo_type)
