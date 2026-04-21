"""Merge logic for syncing local .env with team encrypted secrets."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from augint_tools.team_secrets.models import ConflictEntry, MergeResult
from augint_tools.team_secrets.sops import decrypt_file, encrypt_content


def parse_dotenv_content(content: str) -> dict[str, str]:
    """Parse dotenv content string into a key-value dict.

    Handles:
    - KEY=VALUE (simple assignment)
    - KEY="VALUE" (quoted values, strips quotes)
    - KEY='VALUE' (single-quoted values, strips quotes)
    - # comments (skipped)
    - blank lines (skipped)
    - export KEY=VALUE (strips export prefix)
    """
    result: dict[str, str] = {}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Strip 'export ' prefix
        if stripped.startswith("export "):
            stripped = stripped[7:]

        if "=" not in stripped:
            continue

        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip()

        # Strip surrounding quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]

        # Strip inline comments (only for unquoted values)
        if " #" in value and not (stripped.partition("=")[2].strip().startswith('"')):
            value = value.split(" #")[0].strip()

        if key:
            result[key] = value

    return result


def serialize_dotenv(data: dict[str, str]) -> str:
    """Serialize a key-value dict into dotenv format.

    Values containing special characters are quoted.
    """
    lines: list[str] = []
    for key, value in sorted(data.items()):
        # Quote values with spaces, #, or newlines
        if any(c in value for c in (" ", "#", "\n", "'", '"')):
            # Use double quotes, escape internal double quotes
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{key}="{escaped}"')
        else:
            lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n" if lines else ""


def compute_merge(
    team_data: dict[str, str],
    local_data: dict[str, str],
) -> MergeResult:
    """Compute the merge between team and local env data.

    Does NOT resolve conflicts -- returns them for the caller to handle.

    Args:
        team_data: Key-value pairs from the team encrypted file.
        local_data: Key-value pairs from the local .env file.

    Returns:
        MergeResult with merged data (conflicts unresolved), additions, and conflicts.
    """
    all_keys = set(team_data.keys()) | set(local_data.keys())
    merged: dict[str, str] = {}
    additions: list[str] = []
    conflicts: list[ConflictEntry] = []
    unchanged: list[str] = []

    for key in sorted(all_keys):
        in_team = key in team_data
        in_local = key in local_data

        if in_team and not in_local:
            # Only in team: keep team value
            merged[key] = team_data[key]
        elif in_local and not in_team:
            # Only in local: mark as addition
            merged[key] = local_data[key]
            additions.append(key)
        elif team_data[key] == local_data[key]:
            # Same value: no conflict
            merged[key] = team_data[key]
            unchanged.append(key)
        else:
            # Different values: conflict
            conflicts.append(
                ConflictEntry(
                    key=key,
                    local_value=local_data[key],
                    team_value=team_data[key],
                )
            )
            # Don't add to merged yet -- caller must resolve

    return MergeResult(
        merged=merged,
        additions=additions,
        conflicts=conflicts,
        unchanged=unchanged,
    )


def resolve_conflicts_interactive(conflicts: list[ConflictEntry]) -> dict[str, str]:
    """Interactively resolve conflicts by prompting the user for each key.

    Returns a dict of resolved key-value pairs.
    """
    resolved: dict[str, str] = {}

    click.echo("\nConflicts detected between local and team values:")
    click.echo("-" * 60)

    for conflict in conflicts:
        click.echo(f"\n  Key: {conflict.key}")
        click.echo(f"  [T] Team:  {_truncate(conflict.team_value, 60)}")
        click.echo(f"  [L] Local: {_truncate(conflict.local_value, 60)}")

        choice = click.prompt(
            "  Keep [T]eam, [L]ocal, or [C]ustom?",
            type=click.Choice(["t", "l", "c"], case_sensitive=False),
            default="t",
        )

        if choice.lower() == "t":
            resolved[conflict.key] = conflict.team_value
        elif choice.lower() == "l":
            resolved[conflict.key] = conflict.local_value
        else:
            custom = click.prompt(f"  Enter value for {conflict.key}")
            resolved[conflict.key] = custom

    return resolved


def perform_team_sync(
    team_repo_path: Path,
    project: str,
    env: str,
    key_file: Path,
    *,
    local_env_path: Path | None = None,
    dry_run: bool = False,
    diff_only: bool = False,
    write_local_env: bool = False,
    no_commit: bool = False,
    no_push: bool = False,
) -> dict:
    """Perform the full sync workflow between local and team secrets.

    Returns a result dict for CommandResponse.

    Args:
        team_repo_path: Path to the team secrets repo.
        project: Project name.
        env: Environment name (dev, prod, etc.).
        key_file: Path to the age private key file.
        local_env_path: Path to local .env (default: ./.env in cwd).
        dry_run: If True, show what would change without modifying anything.
        diff_only: If True, only show diff and exit.
        write_local_env: If True, write merged result to local .env.
        no_commit: If True, skip committing to team repo.
        no_push: If True, skip pushing team repo.
    """
    from augint_tools.team_secrets.repo import commit_and_push, get_encrypted_env_path

    encrypted_path = get_encrypted_env_path(team_repo_path, project, env)
    if not encrypted_path.exists():
        raise click.ClickException(
            f"No encrypted file at {encrypted_path}. "
            f"Run: ai-tools team-secrets <team> admin init-project {project}"
        )

    # Decrypt team file
    team_content = decrypt_file(encrypted_path, key_file)
    team_data = parse_dotenv_content(team_content)

    # Load local .env if it exists
    if local_env_path is None:
        local_env_path = Path.cwd() / ".env"

    local_data: dict[str, str] = {}
    if local_env_path.exists():
        local_data = parse_dotenv_content(local_env_path.read_text())

    # If no local file, just use team data as-is
    if not local_data:
        if diff_only:
            return {"diff": "No local .env to compare", "team_keys": list(team_data.keys())}
        if write_local_env:
            if not dry_run:
                local_env_path.write_text(serialize_dotenv(team_data))
            return {
                "action": "wrote_local",
                "keys_written": len(team_data),
                "dry_run": dry_run,
            }
        return {"action": "no_local_env", "team_keys": list(team_data.keys())}

    # Compute merge
    merge = compute_merge(team_data, local_data)

    # Diff-only mode
    if diff_only:
        return {
            "additions": merge.additions,
            "conflicts": [
                {"key": c.key, "local": c.local_value, "team": c.team_value}
                for c in merge.conflicts
            ],
            "unchanged_count": len(merge.unchanged),
        }

    # Handle conflicts
    resolved: dict[str, str] = {}
    if merge.conflicts:
        if sys.stdin.isatty() and not dry_run:
            resolved = resolve_conflicts_interactive(merge.conflicts)
        else:
            # Non-interactive: fail with conflict details
            return {
                "status": "action-required",
                "conflicts": [
                    {"key": c.key, "local": c.local_value, "team": c.team_value}
                    for c in merge.conflicts
                ],
                "message": f"{len(merge.conflicts)} keys conflict between local and team",
            }

    # Build final merged data
    final_data = dict(merge.merged)
    final_data.update(resolved)

    prefix = "[DRY RUN] " if dry_run else ""
    actions_taken: list[str] = []

    # Write back to team repo (re-encrypt)
    if not dry_run:
        new_content = serialize_dotenv(final_data)
        encrypt_content(
            new_content,
            encrypted_path,
            key_file,
            sops_config=team_repo_path / ".sops.yaml",
        )
        actions_taken.append("encrypted")
    else:
        actions_taken.append("would encrypt")

    # Commit and push team repo
    if not no_commit and not dry_run:
        if not no_push:
            commit_and_push(
                team_repo_path,
                f"sync: update {project}/{env}.enc.env",
            )
            actions_taken.append("committed and pushed")
        else:
            # Just commit, no push
            import subprocess

            subprocess.run(["git", "add", "-A"], cwd=team_repo_path, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", f"sync: update {project}/{env}.enc.env"],
                cwd=team_repo_path,
                capture_output=True,
            )
            actions_taken.append("committed (not pushed)")

    # Write local .env
    if write_local_env:
        if not dry_run:
            local_env_path.write_text(serialize_dotenv(final_data))
        actions_taken.append(f"{prefix}wrote local .env")

    return {
        "actions": actions_taken,
        "additions": merge.additions,
        "conflicts_resolved": len(merge.conflicts),
        "unchanged": len(merge.unchanged),
        "total_keys": len(final_data),
        "dry_run": dry_run,
    }


def _truncate(s: str, max_len: int) -> str:
    """Truncate a string with ellipsis if too long."""
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "..."
