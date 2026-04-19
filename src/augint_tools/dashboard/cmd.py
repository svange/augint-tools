"""Click command for the dashboard (``ai-tools dashboard``).

Widget-per-card Textual dashboard; pluggable layouts and themes.
"""

from __future__ import annotations

import click
from loguru import logger
from rich import print

from ._common import configure_logging, get_github_client, load_env_config
from ._helpers import (
    list_repos,
    select_org_interactive,
    select_repos_interactive,
    strip_dotfile_repos,
    warn_rate_limit,
)
from .layouts import list_layouts
from .prefs import load_prefs
from .themes import list_themes


@click.command("dashboard")
@click.option("--all", "-a", "show_all", is_flag=True, help="Show all repos for the account/org.")
@click.option("--interactive", "-i", is_flag=True, help="Interactively select repos to monitor.")
@click.option(
    "--refresh-seconds",
    type=int,
    default=600,
    show_default=True,
    help="Refresh interval in seconds.",
)
@click.option("--org", type=str, default=None, help="Specify organization directly.")
@click.option(
    "--theme",
    type=str,
    default=None,
    help="Dashboard theme (restored from last session if omitted).",
)
@click.option(
    "--layout",
    type=str,
    default=None,
    help="Initial layout strategy (restored from last session if omitted).",
)
@click.option(
    "--stale-days",
    type=int,
    default=5,
    show_default=True,
    help="Days before a PR is considered stale.",
)
@click.option(
    "--env-auth",
    "--dotenv-auth",
    is_flag=True,
    help="Use GH_TOKEN from .env instead of gh auth token/keyring.",
)
@click.option(
    "--no-refresh",
    is_flag=True,
    help="Render from the on-disk cache without hitting the GitHub API (fast startup for testing).",
)
@click.option("--verbose", "-v", is_flag=True, help="Show additional detail.")
@click.pass_context
def dashboard_command(
    ctx: click.Context,
    show_all: bool,
    interactive: bool,
    refresh_seconds: int,
    org: str | None,
    theme: str | None,
    layout: str | None,
    stale_days: int,
    env_auth: bool,
    no_refresh: bool,
    verbose: bool,
) -> None:
    """Interactive Textual health dashboard for GitHub repositories."""
    configure_logging(verbose)

    try:
        from .app import run_dashboard
    except ImportError as exc:
        raise click.ClickException(
            "textual is required for dashboard. Install with: uv add 'augint-tools[tui]'"
        ) from exc

    # Restore saved preferences for options the user didn't explicitly pass.
    prefs = load_prefs()
    if theme is None:
        theme = prefs.theme_name
    if layout is None:
        layout = prefs.layout_name

    themes = list_themes()
    if theme not in themes:
        raise click.ClickException(f"Unknown theme '{theme}'. Available: {', '.join(themes)}")
    layouts = list_layouts()
    if layout not in layouts:
        raise click.ClickException(f"Unknown layout '{layout}'. Available: {', '.join(layouts)}")

    _, gh_account, _ = load_env_config()
    auth_source = "dotenv" if env_auth else "auto"
    if env_auth:
        logger.debug("Dashboard auth mode forced to .env (--env-auth).")
    g = get_github_client(auth_source=auth_source)

    if interactive:
        owner = org if org else select_org_interactive(g)
        all_repos = strip_dotfile_repos(list_repos(g, owner))
        repos = select_repos_interactive(all_repos)
    elif show_all:
        owner = org if org else gh_account
        if not owner:
            raise click.ClickException(
                "GH_ACCOUNT must be set in .env or environment, or use --org."
            )
        repos = strip_dotfile_repos(list_repos(g, owner))
        if not repos:
            raise click.ClickException(f"No repositories found for {owner}.")
    else:
        gh_repo, gh_account_env, _ = load_env_config()
        if not gh_repo or not gh_account_env:
            raise click.ClickException(
                "GH_REPO and GH_ACCOUNT must be set in .env or environment. "
                "Use --all or --interactive for multi-repo mode."
            )
        from ._common import get_github_repo

        repos = [get_github_repo(gh_account_env, gh_repo, auth_source=auth_source)]

    warn_rate_limit(len(repos), refresh_seconds)

    org_name = org or gh_account or ""
    health_config = {"stale_pr_days": stale_days}

    try:
        run_dashboard(
            repos,
            refresh_seconds=refresh_seconds,
            theme=theme,
            layout=layout,
            health_config=health_config,
            org_name=org_name,
            skip_refresh=no_refresh,
            github_client=g,
            auto_discover=show_all,
            saved_prefs=prefs,
        )
    except KeyboardInterrupt:
        print("\n[dim]Dashboard stopped.[/dim]")
