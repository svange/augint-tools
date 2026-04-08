"""Command execution helpers for augint-tools."""

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


@click.command(context_settings={"ignore_unknown_options": True})
@click.argument("command", nargs=-1, type=click.UNPROCESSED)
def foreach(command: tuple[str, ...]) -> None:
    """Run a command across configured repos."""
    _emit_stub("foreach", cwd=str(Path.cwd()), command=list(command))


@click.command()
@click.option("--json", "as_json", is_flag=True, default=False)
def test(as_json: bool) -> None:
    """Run configured tests."""
    _emit_stub("test", cwd=str(Path.cwd()), json=as_json)


@click.command()
@click.option("--fix", is_flag=True, default=False)
@click.option("--json", "as_json", is_flag=True, default=False)
def lint(fix: bool, as_json: bool) -> None:
    """Run configured lint and quality checks."""
    _emit_stub("lint", cwd=str(Path.cwd()), fix=fix, json=as_json)
