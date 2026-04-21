"""Ephemeral checkout of the team secrets repo.

Clones the secrets repo to a temp directory, yields the path for operations,
then commits, pushes, and cleans up. No persistent local clone.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from loguru import logger

DEFAULT_ORG = "augmenting-integrations"


def secrets_repo_slug(team: str, org: str = DEFAULT_ORG) -> str:
    """Return the GitHub slug for a team's secrets repo."""
    return f"{org}/{team}-secrets"


@contextmanager
def ephemeral_checkout(
    team: str,
    org: str = DEFAULT_ORG,
    *,
    push_on_exit: bool = True,
) -> Generator[Path, None, None]:
    """Clone the team secrets repo to a temp dir, yield its path, then cleanup.

    On context exit:
    - If there are uncommitted changes and push_on_exit is True: stage, commit, push.
    - Always delete the temp directory.

    Usage::

        with ephemeral_checkout("woxom") as repo_path:
            # repo_path is a Path to the cloned repo in a temp dir
            # do work: edit files, run sops, etc.
            # changes are auto-committed and pushed on exit
    """
    slug = secrets_repo_slug(team, org)
    tmp_dir = tempfile.mkdtemp(prefix=f"team-secrets-{team}-")
    repo_path = Path(tmp_dir)

    try:
        # Clone via gh CLI (handles auth via gh's SSO/keyring)
        logger.debug(f"Cloning {slug} to {repo_path}")
        result = subprocess.run(
            ["gh", "repo", "clone", slug, str(repo_path), "--", "--depth=1"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to clone {slug}: {result.stderr.strip()}\n"
                f"Make sure the repo exists and you have access: gh repo view {slug}"
            )

        yield repo_path

        # On successful exit: commit and push if there are changes
        if push_on_exit and _has_changes(repo_path):
            _commit_and_push(repo_path)

    finally:
        # Always cleanup
        shutil.rmtree(tmp_dir, ignore_errors=True)
        logger.debug(f"Cleaned up {tmp_dir}")


def _has_changes(repo_path: Path) -> bool:
    """Check if the repo has any uncommitted changes."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def _commit_and_push(repo_path: Path, message: str | None = None) -> None:
    """Stage all changes, commit, and push."""
    if message is None:
        message = "chore: update secrets via ai-tools"

    subprocess.run(
        ["git", "add", "-A"],
        cwd=repo_path,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=repo_path,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "push"],
        cwd=repo_path,
        capture_output=True,
        check=True,
    )
    logger.debug("Committed and pushed changes to secrets repo")
