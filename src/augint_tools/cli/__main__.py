"""augint-tools CLI entry point."""

import logging
import sys

import click

from augint_tools import __version__
from augint_tools.cli.commands.monorepo import monorepo
from augint_tools.cli.commands.project import init
from augint_tools.cli.commands.repo import repo


@click.group()
@click.version_option(version=__version__, prog_name="ai-tools")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Enable debug logging.")
@click.pass_context
def cli(ctx, verbose):
    """CLI for AI-assisted repository and workspace workflows."""
    ctx.ensure_object(dict)
    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(name)s: %(message)s")
        logging.debug(f"augint-tools v{__version__} initialized")


# Top-level commands
cli.add_command(init)

# Command groups
cli.add_command(repo)
cli.add_command(monorepo)


def main():
    """Main entry point."""
    try:
        cli()
    except Exception as exc:  # pragma: no cover - top-level CLI guard
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
