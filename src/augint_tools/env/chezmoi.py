"""Back up .env files to chezmoi and optionally sync to GitHub."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import click
from loguru import logger

from augint_tools.env.sync import perform_sync


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


def chezmoi_backup(
    filename: str = ".env",
    *,
    sync_github: bool = True,
    verbose: bool = False,
    dry_run: bool = False,
) -> dict[str, object]:
    """Back up an env file to chezmoi and optionally sync secrets to GitHub.

    Returns a result dict suitable for CommandResponse.
    """
    ensure_chezmoi()

    env_path = Path(filename).resolve()
    if not env_path.exists():
        raise click.ClickException(f"File not found: {env_path}")

    project_name = Path.cwd().name

    click.echo(f"Adding {filename} to chezmoi...")
    run_chezmoi(["add", str(env_path)], dry_run=dry_run, verbose=verbose)

    run_chezmoi(["git", "add", "--", "."], dry_run=dry_run, verbose=verbose)

    status_result = run_chezmoi(
        ["git", "status", "--", "--porcelain"], dry_run=dry_run, verbose=verbose
    )
    status_output = status_result.stdout.strip()

    chezmoi_committed = False
    if status_output or dry_run:
        message = build_commit_message(project_name, status_output)
        click.echo("Committing to chezmoi...")
        run_chezmoi(["git", "commit", "--", "-m", message], dry_run=dry_run, verbose=verbose)

        click.echo("Syncing with chezmoi remote...")
        run_chezmoi(["git", "pull", "--", "--rebase"], dry_run=dry_run, verbose=verbose)
        run_chezmoi(["git", "push"], dry_run=dry_run, verbose=verbose)
        chezmoi_committed = True

    result: dict[str, object] = {"chezmoi_committed": chezmoi_committed}

    if sync_github:
        click.echo("Syncing secrets to GitHub...")
        sync_result = asyncio.run(perform_sync(filename, dry_run))
        result["secrets_synced"] = len(sync_result["secrets"])
        result["variables_synced"] = len(sync_result["variables"])
    else:
        result["secrets_synced"] = 0
        result["variables_synced"] = 0

    return result
