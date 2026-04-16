"""augint-tools CLI entry point."""

import logging
import sys

import click

from augint_tools import __version__
from augint_tools.cli.commands.env import gh, sync
from augint_tools.cli.commands.init import init
from augint_tools.cli.commands.workspace import workspace
from augint_tools.dashboard.cmd import dashboard_command


@click.group()
@click.version_option(version=__version__, prog_name="ai-tools")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Enable debug logging.")
@click.option("--json", "json_mode", is_flag=True, default=False, help="Output as JSON.")
@click.pass_context
def cli(ctx, verbose, json_mode):
    """CLI for AI-assisted repository and workspace workflows."""
    ctx.ensure_object(dict)
    ctx.obj["json_mode"] = json_mode
    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(name)s: %(message)s")
        logging.debug(f"augint-tools v{__version__} initialized")


cli.add_command(gh)
cli.add_command(sync)
cli.add_command(workspace)
cli.add_command(init)
cli.add_command(dashboard_command)


def main():
    """Main entry point."""
    try:
        cli()
    except Exception as exc:  # pragma: no cover - top-level CLI guard
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
