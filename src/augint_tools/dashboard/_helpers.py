"""Interactive repo-selection + rate-limit helpers used by the dashboard command."""

from __future__ import annotations

import click
from github import Github
from github.GithubException import GithubException, UnknownObjectException
from github.Repository import Repository
from loguru import logger
from rich import print
from rich.text import Text


def strip_dotfile_repos(repos: list[Repository]) -> list[Repository]:
    """Drop repos whose short name starts with '.' (e.g. .github, .discussions).

    These are GitHub's special community-health repos; they never contain
    application code and muddy the dashboard.
    """
    return [r for r in repos if not getattr(r, "name", "").startswith(".")]


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


def get_viewer_login(g: Github) -> str:
    """Return the login of the authenticated user."""
    try:
        return g.get_user().login
    except GithubException:
        return ""


def list_user_orgs(g: Github) -> list[str]:
    """Return login names of all organizations the authenticated user belongs to."""
    try:
        return [org.login for org in g.get_user().get_orgs()]
    except GithubException:
        return []


def list_repos_multi(g: Github, owners: list[str]) -> list[Repository]:
    """List non-archived repos across multiple owners (users/orgs).

    Deduplicates by ``full_name`` in case the same repo appears under
    multiple owners (shouldn't happen, but defensive).
    """
    seen: set[str] = set()
    result: list[Repository] = []
    for owner in owners:
        try:
            for repo in list_repos(g, owner):
                if repo.full_name not in seen:
                    seen.add(repo.full_name)
                    result.append(repo)
        except Exception:
            logger.warning(f"Failed to list repos for '{owner}', skipping.")
    return result


def warn_rate_limit(repo_count: int, refresh_seconds: int) -> None:
    """Warn if the configured refresh would make rate-limit pressure likely.

    With the GraphQL workspace fetcher, each refresh costs ~1 query per 25
    repos plus a tiny per-failing-repo REST detail lookup. Budget is GitHub's
    5000-point/hour GraphQL cap shared with the user's other tooling. The
    threshold below is conservative -- it warns well before the user would
    actually hit a block.
    """
    if refresh_seconds <= 0:
        return
    refreshes_per_hour = 3600 // refresh_seconds
    queries_per_refresh = max(1, (repo_count + 24) // 25)
    estimated_hourly = refreshes_per_hour * queries_per_refresh
    if estimated_hourly > 1200:
        logger.warning(
            f"~{estimated_hourly} GraphQL queries/hour estimated for {repo_count} repos "
            f"at {refresh_seconds}s refresh. Budget is 5000 points/hour. "
            f"Consider increasing --refresh-seconds."
        )
        print(
            f"[yellow]Warning: ~{estimated_hourly} GraphQL queries/hour estimated. "
            f"Consider using --refresh-seconds {max(refresh_seconds, 60)}.[/yellow]"
        )
