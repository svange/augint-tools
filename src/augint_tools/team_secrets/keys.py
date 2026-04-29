"""Key bootstrap, caching, and verification for team secrets."""

from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path

import click
import yaml
from loguru import logger

from augint_tools.config import get_augint_home
from augint_tools.team_secrets.age import decrypt_file_with_password
from augint_tools.team_secrets.checkout import DEFAULT_ORG
from augint_tools.team_secrets.models import TeamConfig


def get_config_dir() -> Path:
    """Return the augint config directory (~/.augint/)."""
    return get_augint_home()


def get_teams_config_path() -> Path:
    """Return path to the teams configuration file."""
    return get_config_dir() / "teams.yaml"


def get_key_cache_path(team: str) -> Path:
    """Return the path where a team's decrypted age key is cached."""
    return get_config_dir() / "keys" / team / "age-key.txt"


def load_teams_config() -> dict[str, TeamConfig]:
    """Load all team configurations from ~/.augint/teams.yaml."""
    config_path = get_teams_config_path()
    if not config_path.exists():
        return {}

    with open(config_path) as f:
        raw = yaml.safe_load(f) or {}

    teams: dict[str, TeamConfig] = {}
    for name, data in raw.items():
        if isinstance(data, dict):
            teams[name] = TeamConfig(
                name=name,
                org=data.get("org", DEFAULT_ORG),
                username=data.get("username", ""),
            )
    return teams


def load_team_config(team: str) -> TeamConfig | None:
    """Load configuration for a specific team. Returns None if not found."""
    return load_teams_config().get(team)


def save_team_config(config: TeamConfig) -> None:
    """Save or update a team's configuration in ~/.augint/teams.yaml."""
    config_path = get_teams_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict = {}
    if config_path.exists():
        with open(config_path) as f:
            existing = yaml.safe_load(f) or {}

    existing[config.name] = {
        "org": config.org,
        "username": config.username,
    }

    with open(config_path, "w") as f:
        yaml.safe_dump(existing, f, default_flow_style=False)


def resolve_org(team: str, org_flag: str | None = None) -> str:
    """Resolve the GitHub org for a team.

    Order: explicit --org flag > teams.yaml > default.
    """
    if org_flag:
        return org_flag
    config = load_team_config(team)
    if config:
        return config.org
    return DEFAULT_ORG


def detect_project_name(path: Path | None = None) -> str | None:
    """Detect the current project name from the git remote.

    Reads the git remote URL and extracts the repo name.
    Returns None if not in a git repo or can't parse the remote.
    """
    from augint_tools.git.repo import extract_repo_slug, get_remote_url

    remote_url = get_remote_url(path)
    if not remote_url:
        return None
    slug = extract_repo_slug(remote_url)
    if not slug or "/" not in slug:
        return None
    return slug.split("/", 1)[1]


def resolve_github_username() -> str | None:
    """Resolve the current GitHub username via gh CLI."""
    try:
        result = subprocess.run(
            ["gh", "api", "user", "--jq", ".login"],
            capture_output=True,
            text=True,
            check=True,
        )
        username = result.stdout.strip()
        return username if username else None
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None


def bootstrap_key(
    team: str,
    repo_path: Path,
    username: str,
    password: str,
) -> Path:
    """Bootstrap the local age key from the team repo's encrypted key file.

    Finds keys/<username>.key.enc, decrypts with password, caches locally.
    repo_path is the ephemeral checkout of the secrets repo.

    Returns the path to the cached decrypted key.
    """
    encrypted_key_path = repo_path / "keys" / f"{username}.key.enc"
    if not encrypted_key_path.exists():
        raise FileNotFoundError(
            f"No encrypted key found for user '{username}' at {encrypted_key_path}. "
            f"Ask a team admin to run: ai-tools team-secrets {team} admin add-user {username}"
        )

    # Decrypt with password
    decrypted_content = decrypt_file_with_password(encrypted_key_path, password)

    # Cache locally
    cache_path = get_key_cache_path(team)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(decrypted_content)

    # Set restrictive permissions (not applicable on Windows)
    if sys.platform != "win32":
        cache_path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 600

    logger.debug(f"Cached decrypted key at {cache_path}")
    return cache_path


def verify_key_permissions(key_path: Path) -> bool:
    """Verify the key file has safe permissions (600 on Unix)."""
    if sys.platform == "win32":
        return True  # Can't check on Windows

    if not key_path.exists():
        return False

    mode = key_path.stat().st_mode
    # Check that only owner has read/write
    return (mode & stat.S_IRWXG) == 0 and (mode & stat.S_IRWXO) == 0


def get_cached_key(team: str) -> Path | None:
    """Return the cached key path if it exists and is valid."""
    cache_path = get_key_cache_path(team)
    if cache_path.exists() and cache_path.stat().st_size > 0:
        return cache_path
    return None


def require_key(team: str) -> Path:
    """Get the cached key path, raising an error if not available.

    Use this in commands that require a decrypted key to operate.
    """
    key_path = get_cached_key(team)
    if key_path is None:
        raise click.ClickException(
            f"No cached key for team '{team}'. Run: ai-tools team-secrets {team} setup"
        )
    return key_path
