"""GitHub pull request operations."""

import json
from dataclasses import dataclass

from augint_tools.github.cli import run_gh


@dataclass
class PullRequest:
    """GitHub pull request information."""

    number: int
    title: str
    state: str
    url: str
    head_ref: str


def get_open_prs(branch: str | None = None, repo: str | None = None) -> list[PullRequest]:
    """
    Get open pull requests.

    Args:
        branch: Filter by head branch (uses current branch if None)
        repo: Repository in "owner/repo" format (uses current repo if None)

    Returns:
        List of PullRequest objects
    """
    try:
        args = ["pr", "list", "--json", "number,title,state,url,headRefName"]

        if repo:
            args.extend(["--repo", repo])

        if branch:
            args.extend(["--head", branch])

        result = run_gh(args, check=False)
        if result.returncode != 0:
            return []

        data = json.loads(result.stdout)
        prs = []
        for item in data:
            prs.append(
                PullRequest(
                    number=item["number"],
                    title=item["title"],
                    state=item["state"],
                    url=item["url"],
                    head_ref=item["headRefName"],
                )
            )
        return prs
    except Exception:
        return []


def create_pr(
    title: str,
    base: str,
    head: str | None = None,
    body: str = "",
    repo: str | None = None,
) -> str | None:
    """
    Create a pull request.

    Args:
        title: PR title
        base: Base branch
        head: Head branch (uses current branch if None)
        body: PR body/description
        repo: Repository in "owner/repo" format (uses current repo if None)

    Returns:
        PR URL if successful, None otherwise
    """
    try:
        args = ["pr", "create", "--title", title, "--base", base, "--body", body]

        if repo:
            args.extend(["--repo", repo])

        if head:
            args.extend(["--head", head])

        result = run_gh(args, check=False)
        if result.returncode != 0:
            return None

        # gh pr create outputs the URL
        return str(result.stdout.strip())
    except Exception:
        return None


def enable_automerge(pr_number: int, repo: str | None = None) -> bool:
    """
    Enable auto-merge for a pull request.

    Args:
        pr_number: PR number
        repo: Repository in "owner/repo" format (uses current repo if None)

    Returns:
        True if successful
    """
    try:
        args = ["pr", "merge", str(pr_number), "--auto", "--squash"]

        if repo:
            args.extend(["--repo", repo])

        result = run_gh(args, check=False)
        return result.returncode == 0
    except Exception:
        return False
