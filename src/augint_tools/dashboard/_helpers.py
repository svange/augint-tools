"""Interactive repo-selection + rate-limit helpers used by the dashboard command."""

from __future__ import annotations

import click
from github import Github
from github.GithubException import GithubException, UnknownObjectException
from github.Repository import Repository
from loguru import logger
from rich import print
from rich.text import Text


def list_repos(g: Github, owner: str) -> list[Repository]:
    """List non-archived repos for a user or organization.

    Tries organization first (which includes private repos the token
    can see), then falls back to the authenticated user's own repos.
    """
    try:
        viewer = g.get_user().login
    except GithubException:
        viewer = "<unknown>"
    logger.debug(f"Listing repos for '{owner}' as '{viewer}'.")

    try:
        repos = list(g.get_organization(owner).get_repos(type="all"))
        logger.debug(f"Resolved '{owner}' as an organization.")
    except (UnknownObjectException, GithubException):
        # Not an org -- treat as a user.  If owner matches the
        # authenticated user, get_user().get_repos() returns private
        # repos; otherwise only public repos are visible.
        logger.debug(f"Organization lookup failed for '{owner}'. Falling back to user repos.")
        repos = list(g.get_user(owner).get_repos())

    visible_repos = [r for r in repos if not r.archived]
    private_count = sum(1 for repo in visible_repos if getattr(repo, "private", False) is True)
    public_count = len(visible_repos) - private_count
    logger.debug(
        f"Found {len(visible_repos)} non-archived repos for '{owner}' "
        f"({private_count} private, {public_count} public)."
    )
    return visible_repos


def select_org_interactive(g: Github) -> str:
    """Prompt the user to select an organization or personal account."""
    user = g.get_user()
    login: str = user.login
    orgs = list(user.get_orgs())

    if not orgs:
        return login

    print(Text.from_markup("\n[bold]Select account:[/bold]"))
    choices: list[str] = []
    for i, org in enumerate(orgs, 1):
        org_login: str = org.login
        print(f"  {i}. {org_login}")
        choices.append(org_login)
    personal_idx = len(choices) + 1
    print(f"  {personal_idx}. {login} [dim](personal)[/dim]")
    choices.append(login)

    while True:
        raw: int = click.prompt("Choice", type=int)
        if 1 <= raw <= len(choices):
            selected: str = choices[raw - 1]
            return selected
        print("[red]Invalid selection.[/red]")


def select_repos_interactive(repos: list[Repository]) -> list[Repository]:
    """Prompt the user to select repos from a numbered list."""
    if not repos:
        raise click.ClickException("No repositories found.")

    print(Text.from_markup("\n[bold]Select repositories[/bold] (comma-separated, e.g. 1,3,5):"))
    for i, repo in enumerate(repos, 1):
        print(f"  {i}. {repo.name}")

    while True:
        raw = click.prompt("Selection")
        try:
            indices = [int(x.strip()) for x in raw.split(",")]
            selected = [repos[i - 1] for i in indices if 1 <= i <= len(repos)]
        except (ValueError, IndexError):
            selected = []
        if selected:
            return selected
        print("[red]Invalid selection. Try again.[/red]")


def warn_rate_limit(repo_count: int, refresh_seconds: int) -> None:
    """Warn if estimated API usage would exceed GitHub rate limits."""
    calls_per_repo = 7
    hourly = repo_count * calls_per_repo * (3600 // refresh_seconds)
    if hourly > 4000:
        logger.warning(
            f"Estimated ~{hourly} API calls/hour for {repo_count} repos at "
            f"{refresh_seconds}s refresh. GitHub allows 5000/hour. "
            f"Consider increasing --refresh-seconds."
        )
        print(
            f"[yellow]Warning: ~{hourly} API calls/hour estimated. "
            f"Consider using --refresh-seconds {max(refresh_seconds, 120)}.[/yellow]"
        )
