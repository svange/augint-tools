"""Push .env secrets and variables to GitHub repository settings.

Secrets and variables are created/updated/deleted to match the .env file exactly.
Classification is handled by augint_tools.env.classify.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from dotenv import load_dotenv
from loguru import logger

from augint_tools.env.auth import get_github_repo
from augint_tools.env.classify import partition_env


async def _sync_secrets(repo: Any, env_data: dict[str, str], dry_run: bool) -> list[str]:
    """Create/update/delete GitHub secrets to match env_data."""
    existing = await asyncio.to_thread(repo.get_secrets)
    existing_names = [s.name for s in existing]
    prefix = "[DRY RUN] " if dry_run else ""
    tasks: list[Any] = []
    synced: list[str] = []

    for name, value in env_data.items():
        action = "Updating" if name in existing_names else "Creating"
        logger.info(f"{prefix}{action} secret {name}...")
        tasks.append(asyncio.to_thread(repo.create_secret, name, value))
        synced.append(name)

    for name in existing_names:
        if name not in env_data:
            logger.info(f"{prefix}Deleting secret {name}...")
            tasks.append(asyncio.to_thread(repo.delete_secret, name))

    if not dry_run:
        await asyncio.gather(*tasks)
    return synced


async def _sync_variables(repo: Any, env_data: dict[str, str], dry_run: bool) -> list[str]:
    """Create/update/delete GitHub variables to match env_data."""
    existing = await asyncio.to_thread(repo.get_variables)
    existing_names = [v.name for v in existing]
    prefix = "[DRY RUN] " if dry_run else ""
    tasks: list[Any] = []
    synced: list[str] = []

    for name, value in env_data.items():
        if name in existing_names:
            logger.info(f"{prefix}Updating variable {name}...")

            def _delete_then_create(r: Any, n: str, v: str) -> None:
                r.delete_variable(n)
                r.create_variable(n, v)

            tasks.append(asyncio.to_thread(_delete_then_create, repo, name, value))
        else:
            logger.info(f"{prefix}Creating variable {name}...")
            tasks.append(asyncio.to_thread(repo.create_variable, name, value))
        synced.append(name)

    for name in existing_names:
        if name not in env_data:
            logger.info(f"{prefix}Deleting variable {name}...")
            tasks.append(asyncio.to_thread(repo.delete_variable, name))

    if not dry_run:
        await asyncio.gather(*tasks)
    return synced


async def perform_sync(
    filename: str = ".env",
    dry_run: bool = False,
    *,
    force_var: frozenset[str] | set[str] | None = None,
    force_secret: frozenset[str] | set[str] | None = None,
) -> dict[str, list[str]]:
    """Sync .env file to GitHub secrets and variables.

    Returns dict with 'secrets' and 'variables' keys listing synced names.
    """
    if not filename:
        raise ValueError("No filename specified.")

    load_dotenv(str(filename), override=True)
    gh_repo = os.environ.get("GH_REPO", "")
    gh_account = os.environ.get("GH_ACCOUNT", "")

    if not gh_repo or not gh_account:
        raise RuntimeError("GH_REPO and GH_ACCOUNT must be set in .env or environment.")

    secrets, variables = partition_env(filename, force_var=force_var, force_secret=force_secret)

    repo = get_github_repo(gh_account, gh_repo)

    synced_secrets = await _sync_secrets(repo, secrets, dry_run)
    synced_variables = await _sync_variables(repo, variables, dry_run)

    return {"secrets": synced_secrets, "variables": synced_variables}
