"""augint-tools CLI entry point."""

import logging
import sys

import click

from augint_tools import __version__
from augint_tools.cli.commands.ide import ide
from augint_tools.cli.commands.init import init
from augint_tools.cli.commands.repo import repo
from augint_tools.cli.commands.workspace import workspace
from augint_tools.dashboard.cmd import dashboard_command


@click.group()
@click.version_option(version=__version__, prog_name="ai-tools")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Enable debug logging.")
@click.option("--json", "json_mode", is_flag=True, default=False, help="Output as JSON.")
@click.option("--actionable", is_flag=True, default=False, help="Suppress passing/no-op items.")
@click.option(
    "--summary",
    "summary_only",
    is_flag=True,
    default=False,
    help="Emit only rollup and next actions.",
)
@click.pass_context
def cli(ctx, verbose, json_mode, actionable, summary_only):
    """CLI for AI-assisted repository and workspace workflows."""
    ctx.ensure_object(dict)
    ctx.obj["json_mode"] = json_mode
    ctx.obj["actionable"] = actionable
    ctx.obj["summary_only"] = summary_only
    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(name)s: %(message)s")
        logging.debug(f"augint-tools v{__version__} initialized")


# Command groups
cli.add_command(repo)
cli.add_command(workspace)
cli.add_command(ide)
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
