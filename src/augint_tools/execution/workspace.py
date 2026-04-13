"""Workspace orchestration utilities."""

import os
import re
from pathlib import Path

from augint_tools.config import RepoConfig
from augint_tools.git.repo import extract_repo_slug, get_remote_url


def topological_sort(repos: list[RepoConfig]) -> list[RepoConfig]:
    """
    Sort repos in dependency order using topological sort.

    Args:
        repos: List of repo configurations

    Returns:
        Sorted list of repos (dependencies first)

    Raises:
        ValueError: If circular dependency detected
    """
    # Build dependency graph
    repo_map = {repo.name: repo for repo in repos}
    in_degree = {repo.name: 0 for repo in repos}
    adj_list: dict[str, list[str]] = {repo.name: [] for repo in repos}

    for repo in repos:
        for dep in repo.depends_on:
            if dep in repo_map:
                adj_list[dep].append(repo.name)
                in_degree[repo.name] += 1

    # Kahn's algorithm for topological sort
    queue = [name for name in in_degree if in_degree[name] == 0]
    result = []

    while queue:
        current = queue.pop(0)
        result.append(current)

        for neighbor in adj_list[current]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    # Check for cycles
    if len(result) != len(repos):
        raise ValueError("Circular dependency detected in workspace repos")

    # Return repos in sorted order
    return [repo_map[name] for name in result]


def get_repo_path(base_path: Path, repo_config: RepoConfig) -> Path:
    """Get absolute path for a repo, with fallback for sibling layouts.

    Precedence:
        1. WORKSPACE_REPOS_DIR env var: $WORKSPACE_REPOS_DIR/<repo_name>
        2. Configured path (base_path / repo_config.path) if it contains .git/
        3. Sibling path (base_path.parent / repo_config.name) if it contains .git/
        4. Configured path as-is (original behavior)

    Args:
        base_path: Workspace base path
        repo_config: Repository configuration

    Returns:
        Absolute path to repository
    """
    # 1. Explicit override via env var
    repos_dir = os.environ.get("WORKSPACE_REPOS_DIR")
    if repos_dir:
        candidate = Path(repos_dir)
        if not candidate.is_absolute():
            candidate = base_path / candidate
        return candidate / repo_config.name

    # 2. Configured path
    repo_path = Path(repo_config.path)
    if repo_path.is_absolute():
        configured = repo_path
    else:
        configured = base_path / repo_path

    # If configured path has a .git dir, use it (existing behavior, fast path)
    if (configured / ".git").is_dir():
        return configured

    # 3. Sibling fallback: base_path.parent / repo_config.name
    sibling = base_path.parent / repo_config.name
    if (sibling / ".git").is_dir():
        return sibling

    # 4. Fall through to configured path (e.g., repo not yet cloned)
    return configured


def resolve_clone_url(repo_config: RepoConfig, workspace_root: Path) -> str:
    """Resolve the effective clone URL for a repo, handling proxy environments.

    Precedence:
        1. GIT_CLONE_URL_TEMPLATE env var with {slug}, {org}, {repo} placeholders
        2. Proxy URL derived from workspace root's origin remote
        3. repo_config.url as-is

    Args:
        repo_config: Repository configuration with .url
        workspace_root: Path to the workspace root (for reading its origin remote)

    Returns:
        The URL to use for git clone
    """
    slug = extract_repo_slug(repo_config.url)

    # 1. Explicit template override
    template = os.environ.get("GIT_CLONE_URL_TEMPLATE")
    if template and slug:
        org, repo_name = slug.split("/", 1)
        return template.replace("{slug}", slug).replace("{org}", org).replace("{repo}", repo_name)

    # 2. Detect proxy from workspace root's origin
    if slug:
        origin_url = get_remote_url(workspace_root)
        if origin_url and _is_proxy_url(origin_url):
            org, repo_name = slug.split("/", 1)
            return _build_proxy_url(origin_url, org, repo_name)

    # 3. Fall through to configured URL
    return repo_config.url


def _is_proxy_url(url: str) -> bool:
    """Check if a URL matches the Claude Code Web proxy pattern."""
    return bool(re.match(r"https?://local_proxy@127\.0\.0\.1:\d+/git/", url))


def _build_proxy_url(origin_proxy_url: str, org: str, repo: str) -> str:
    """Build a proxy clone URL by substituting org/repo into a proxy origin URL.

    Given: http://local_proxy@127.0.0.1:8080/git/OrigOrg/OrigRepo
    Returns: http://local_proxy@127.0.0.1:8080/git/{org}/{repo}
    """
    match = re.match(r"(https?://local_proxy@127\.0\.0\.1:\d+/git/)", origin_proxy_url)
    if match:
        return f"{match.group(1)}{org}/{repo}"
    # Unreachable when called after _is_proxy_url guard, but satisfy all code paths
    return f"http://local_proxy@127.0.0.1:0/git/{org}/{repo}"
