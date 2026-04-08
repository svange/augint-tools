"""Workspace orchestration utilities."""

from pathlib import Path

from augint_tools.config import RepoConfig


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
    """
    Get absolute path for a repo.

    Args:
        base_path: Workspace base path
        repo_config: Repository configuration

    Returns:
        Absolute path to repository
    """
    repo_path = Path(repo_config.path)
    if repo_path.is_absolute():
        return repo_path
    return base_path / repo_path
