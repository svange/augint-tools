"""Click command for the dashboard (``ai-tools dashboard``).

Widget-per-card Textual dashboard; pluggable layouts and themes.
"""

from __future__ import annotations

from pathlib import Path

import click
from loguru import logger
from rich import print

from ._common import configure_logging, get_github_client, load_env_config
from ._helpers import (
    get_viewer_login,
    list_repos_multi,
    list_user_orgs,
    select_repos_interactive,
    strip_dotfile_repos,
    warn_rate_limit,
)
from .layouts import list_layouts
from .prefs import load_prefs
from .themes import list_themes


def _apply_debug_cache_dir() -> None:
    """Override the cache/prefs directory to ``./.cache/ai-tools-dashboard``.

    Must be called before ``run_dashboard`` but after module-level imports
    have evaluated. Patches the module-level constants in ``_data``,
    ``awsprobe``, and ``prefs`` so every component reads/writes the local
    directory.
    """
    local_cache = Path.cwd() / ".cache" / "ai-tools-dashboard"
    from . import _data, awsprobe, prefs

    _data.CACHE_DIR = local_cache
    _data.CACHE_FILE = local_cache / "tui_cache.json"
    awsprobe._CACHE_DIR = local_cache
    awsprobe._CACHE_FILE = local_cache / "aws_cache.json"
    prefs.PREFS_FILE = local_cache / "dashboard_prefs.json"


@click.command("dashboard")
@click.option("--all", "-a", "show_all", is_flag=True, help="Show all repos for the account/org.")
@click.option("--interactive", "-i", is_flag=True, help="Interactively select repos to monitor.")
@click.option(
    "--refresh-seconds",
    type=int,
    default=60,
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
@click.option(
    "--standards-yaml-url",
    "standards_yaml_url",
    type=str,
    default=None,
    help=(
        "Override the URL the YAML compliance engine fetches standards.yaml from. "
        "Default: the ai-cc-tools main branch via GitHub contents API."
    ),
)
@click.option("--verbose", "-v", is_flag=True, help="Show additional detail.")
@click.option(
    "--log",
    "log_file",
    type=click.Path(),
    default=None,
    help="Write debug logs to a file (tail -f alongside the TUI).",
)
@click.option(
    "--debug",
    is_flag=True,
    help="Enable debug mode: log to ./tui.log and use ./.cache for cache/prefs.",
)
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
    standards_yaml_url: str | None,
    verbose: bool,
    log_file: str | None,
    debug: bool,
) -> None:
    """Interactive Textual health dashboard for GitHub repositories."""
    if debug:
        if log_file is None:
            log_file = "./tui.log"
        _apply_debug_cache_dir()
    configure_logging(verbose, log_file=log_file)

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

    # ------------------------------------------------------------------
    # Multi-org mode (--all or --interactive without --org):
    # Auto-discover the personal account + all organizations the user
    # belongs to.  Orgs the user has explicitly disabled via the in-TUI
    # org manager (persisted in prefs.disabled_orgs) are excluded.
    #
    # Legacy single-org mode (--org): use exactly that org.
    # Legacy single-repo mode (no flags): GH_REPO + GH_ACCOUNT from env.
    # ------------------------------------------------------------------
    health_config: dict = {
        "stale_pr_days": stale_days,
        "standards_engine": {"url": standards_yaml_url},
    }

    multi_org = (show_all or interactive) and not org

    if multi_org:
        viewer = get_viewer_login(g) or gh_account or ""
        if not viewer:
            raise click.ClickException(
                "Could not determine GitHub login. Set GH_ACCOUNT or authenticate with: gh auth login"
            )
        disabled_orgs = set(prefs.disabled_orgs)
        owners: list[str] = [viewer]
        for org_login in list_user_orgs(g):
            if org_login not in owners and org_login not in disabled_orgs:
                owners.append(org_login)

        if interactive:
            all_repos = strip_dotfile_repos(list_repos_multi(g, owners))
            repos = select_repos_interactive(all_repos)
        else:
            repos = strip_dotfile_repos(list_repos_multi(g, owners))
            if not repos:
                raise click.ClickException(f"No repositories found for {', '.join(owners)}.")

        warn_rate_limit(len(repos), refresh_seconds)

        try:
            run_dashboard(
                repos,
                refresh_seconds=refresh_seconds,
                theme=theme,
                layout=layout,
                health_config=health_config,
                owners=owners,
                skip_refresh=no_refresh,
                github_client=g,
                auto_discover=show_all,
                saved_prefs=prefs,
            )
        except KeyboardInterrupt:
            print("\n[dim]Dashboard stopped.[/dim]")
        return

    # Legacy paths: --org or single-repo (no flags).
    if show_all:
        owner = org if org else gh_account
        if not owner:
            raise click.ClickException(
                "GH_ACCOUNT must be set in .env or environment, or use --org."
            )
        from ._helpers import list_repos

        repos = strip_dotfile_repos(list_repos(g, owner))
        if not repos:
            raise click.ClickException(f"No repositories found for {owner}.")
        owners = [owner]
    elif org:
        from ._helpers import list_repos

        repos = strip_dotfile_repos(list_repos(g, org))
        if not repos:
            raise click.ClickException(f"No repositories found for {org}.")
        owners = [org]
    else:
        gh_repo, gh_account_env, _ = load_env_config()
        if not gh_repo or not gh_account_env:
            raise click.ClickException(
                "GH_REPO and GH_ACCOUNT must be set in .env or environment. "
                "Use --all or --interactive for multi-repo mode."
            )
        from ._common import get_github_repo

        repos = [get_github_repo(gh_account_env, gh_repo, auth_source=auth_source)]
        owners = [gh_account_env]

    warn_rate_limit(len(repos), refresh_seconds)

    try:
        run_dashboard(
            repos,
            refresh_seconds=refresh_seconds,
            theme=theme,
            layout=layout,
            health_config=health_config,
            owners=owners,
            skip_refresh=no_refresh,
            github_client=g,
            auto_discover=show_all,
            saved_prefs=prefs,
        )
    except KeyboardInterrupt:
        print("\n[dim]Dashboard stopped.[/dim]")
