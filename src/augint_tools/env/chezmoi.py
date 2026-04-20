"""Back up .env files to chezmoi and optionally sync to GitHub."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from typing import Callable

import click
from loguru import logger

from augint_tools.env.sync import perform_sync

QuietWriter = Callable[[str], None]


def run_chezmoi(
    args: list[str], *, dry_run: bool = False, verbose: bool = False
) -> subprocess.CompletedProcess[str]:
    """Run a chezmoi command. Raises click.ClickException on failure."""
    cmd = ["chezmoi", *args]
    if dry_run:
        click.echo(f"[DRY RUN] Would run: {' '.join(cmd)}")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    logger.debug(f"Running: {cmd}")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if verbose and result.stdout:
        click.echo(result.stdout.rstrip())

    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise click.ClickException(f"chezmoi {' '.join(args)} failed: {detail}")

    return result


def build_commit_message(project_name: str, status_output: str) -> str:
    """Build a commit message from chezmoi git status --porcelain output."""
    files = []
    for line in status_output.strip().splitlines():
        if len(line) > 3:
            files.append(line[3:].strip())

    file_list = ", ".join(files) if files else "env files"
    return f"chezmoi: sync {project_name} env files\n\nFiles: {file_list}"


def ensure_chezmoi() -> None:
    """Verify chezmoi is installed."""
    if not shutil.which("chezmoi"):
        raise click.ClickException(
            "chezmoi is not installed. Install from https://chezmoi.io/install/"
        )


def _run_chezmoi_pipeline(
    filename: str,
    env_path: Path,
    project_name: str,
    *,
    verbose: bool,
    dry_run: bool,
    quiet_writer: QuietWriter | None = None,
) -> bool:
    """Execute the synchronous chezmoi backup pipeline.

    Returns True if a commit was produced (or would be in dry-run).
    """
    if quiet_writer is not None:
        quiet_writer(f"chezmoi: add {filename}")
    logger.info(f"Adding {filename} to chezmoi...")
    run_chezmoi(["add", str(env_path)], dry_run=dry_run, verbose=verbose)

    run_chezmoi(["git", "add", "--", "."], dry_run=dry_run, verbose=verbose)

    status_result = run_chezmoi(
        ["git", "status", "--", "--porcelain"], dry_run=dry_run, verbose=verbose
    )
    status_output = status_result.stdout.strip()

    if not (status_output or dry_run):
        if quiet_writer is not None:
            quiet_writer("chezmoi: no changes")
        return False

    message = build_commit_message(project_name, status_output)
    if quiet_writer is not None:
        quiet_writer("chezmoi: commit")
    logger.info("Committing to chezmoi...")
    run_chezmoi(["git", "commit", "--", "-m", message], dry_run=dry_run, verbose=verbose)

    if quiet_writer is not None:
        quiet_writer("chezmoi: push")
    logger.info("Syncing with chezmoi remote...")
    run_chezmoi(["git", "pull", "--", "--rebase"], dry_run=dry_run, verbose=verbose)
    run_chezmoi(["git", "push"], dry_run=dry_run, verbose=verbose)
    return True


async def _run_github_sync(
    filename: str,
    sync_github: bool,
    dry_run: bool,
    *,
    quiet_writer: QuietWriter | None = None,
) -> dict[str, list[str]] | None:
    """Run the GitHub secrets sync if enabled."""
    if not sync_github:
        return None
    if quiet_writer is not None:
        quiet_writer("github: sync")
    logger.info("Syncing secrets to GitHub...")
    return await perform_sync(filename, dry_run, quiet_writer=quiet_writer)


async def _run_pipelines_concurrently(
    filename: str,
    env_path: Path,
    project_name: str,
    *,
    sync_github: bool,
    verbose: bool,
    dry_run: bool,
    quiet_writer: QuietWriter | None = None,
) -> tuple[bool, dict[str, list[str]] | None]:
    """Run chezmoi and GitHub sync pipelines concurrently.

    Both pipelines run to completion even if one fails, so users see all
    errors in a single invocation. If either fails, raises a
    click.ClickException aggregating the messages.
    """
    chezmoi_task = asyncio.create_task(
        asyncio.to_thread(
            _run_chezmoi_pipeline,
            filename,
            env_path,
            project_name,
            verbose=verbose,
            dry_run=dry_run,
            quiet_writer=quiet_writer,
        )
    )
    github_task = asyncio.create_task(
        _run_github_sync(filename, sync_github, dry_run, quiet_writer=quiet_writer)
    )

    results = await asyncio.gather(chezmoi_task, github_task, return_exceptions=True)
    chezmoi_result, github_result = results

    errors: list[str] = []
    if isinstance(chezmoi_result, BaseException):
        errors.append(f"chezmoi backup failed: {chezmoi_result}")
    if isinstance(github_result, BaseException):
        errors.append(f"GitHub sync failed: {github_result}")

    if errors:
        raise click.ClickException("; ".join(errors))

    # mypy: both are not exceptions here
    assert not isinstance(chezmoi_result, BaseException)
    assert not isinstance(github_result, BaseException)
    return chezmoi_result, github_result


def chezmoi_backup(
    filename: str = ".env",
    *,
    sync_github: bool = True,
    verbose: bool = False,
    dry_run: bool = False,
    quiet_writer: QuietWriter | None = None,
) -> dict[str, object]:
    """Back up an env file to chezmoi and optionally sync secrets to GitHub.

    The chezmoi backup (local subprocess calls) and the GitHub secrets sync
    (remote API calls) run concurrently because they are independent.

    Returns a result dict suitable for CommandResponse.
    """
    ensure_chezmoi()

    env_path = Path(filename).resolve()
    if not env_path.exists():
        raise click.ClickException(f"File not found: {env_path}")

    project_name = Path.cwd().name

    chezmoi_committed, sync_result = asyncio.run(
        _run_pipelines_concurrently(
            filename,
            env_path,
            project_name,
            sync_github=sync_github,
            verbose=verbose,
            dry_run=dry_run,
            quiet_writer=quiet_writer,
        )
    )

    result: dict[str, object] = {"chezmoi_committed": chezmoi_committed}

    if sync_result is not None:
        result["secrets_synced"] = len(sync_result["secrets"])
        result["variables_synced"] = len(sync_result["variables"])
    else:
        result["secrets_synced"] = 0
        result["variables_synced"] = 0

    return result
