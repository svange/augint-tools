"""GitHub issues management."""

import json
from dataclasses import dataclass

from augint_tools.github.cli import run_gh


@dataclass
class Issue:
    """GitHub issue information."""

    number: int
    title: str
    state: str
    labels: list[str]
    url: str


def list_issues(repo: str | None = None, query: str | None = None) -> list[Issue]:
    """
    List GitHub issues.

    Args:
        repo: Repository in "owner/repo" format (uses current repo if None)
        query: Search query or label filter

    Returns:
        List of Issue objects
    """
    try:
        args = ["issue", "list", "--json", "number,title,state,labels,url"]

        if repo:
            args.extend(["--repo", repo])

        if query:
            # Check if query looks like a label
            if not query.startswith("label:") and " " not in query:
                args.extend(["--label", query])
            else:
                args.extend(["--search", query])

        result = run_gh(args, check=False)
        if result.returncode != 0:
            return []

        data = json.loads(result.stdout)
        issues = []
        for item in data:
            issues.append(
                Issue(
                    number=item["number"],
                    title=item["title"],
                    state=item["state"],
                    labels=[label["name"] for label in item.get("labels", [])],
                    url=item["url"],
                )
            )
        return issues
    except Exception:
        return []
