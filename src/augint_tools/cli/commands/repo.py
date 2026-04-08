"""Single repository workflow commands."""

import json
from pathlib import Path

import click
from rich.console import Console

console = Console(stderr=True)


def _emit_stub(command_name: str, **payload: object) -> None:
    payload = {"command": command_name, "scope": "repo", "implemented": False, **payload}
    console.print(json.dumps(payload, indent=2))
    console.print(
        f"[yellow]{command_name} is scaffolded but not implemented yet. Use augint-tools.md as the implementation spec.[/yellow]"
    )


@click.group()
def repo() -> None:
    """Single repository workflow commands."""
    pass


@repo.command()
@click.option("--json", "as_json", is_flag=True, default=False)
def status(as_json: bool) -> None:
    """Show repository status."""
    _emit_stub("status", cwd=str(Path.cwd()), json=as_json)


@repo.command()
@click.argument("query", required=False)
@click.option("--json", "as_json", is_flag=True, default=False)
def issues(query: str | None, as_json: bool) -> None:
    """List issues for this repository."""
    _emit_stub("issues", cwd=str(Path.cwd()), query=query, json=as_json)


@repo.command()
@click.argument("branch_name")
@click.option("--json", "as_json", is_flag=True, default=False)
def branch(branch_name: str, as_json: bool) -> None:
    """Create or switch branch."""
    _emit_stub("branch", cwd=str(Path.cwd()), branch=branch_name, json=as_json)


@repo.command()
@click.option("--json", "as_json", is_flag=True, default=False)
def test(as_json: bool) -> None:
    """Run tests for this repository."""
    _emit_stub("test", cwd=str(Path.cwd()), json=as_json)


@repo.command()
@click.option("--fix", is_flag=True, default=False)
@click.option("--json", "as_json", is_flag=True, default=False)
def lint(fix: bool, as_json: bool) -> None:
    """Run lint and quality checks."""
    _emit_stub("lint", cwd=str(Path.cwd()), fix=fix, json=as_json)


@repo.command()
@click.option("--json", "as_json", is_flag=True, default=False)
def submit(as_json: bool) -> None:
    """Push branch and open PR."""
    _emit_stub("submit", cwd=str(Path.cwd()), json=as_json)
