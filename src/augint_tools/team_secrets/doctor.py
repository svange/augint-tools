"""Health checks for team secrets configuration."""

from __future__ import annotations

import subprocess
from pathlib import Path

from augint_tools.team_secrets.age import is_age_installed
from augint_tools.team_secrets.keys import (
    get_cached_key,
    load_team_config,
    verify_key_permissions,
)
from augint_tools.team_secrets.models import DoctorCheck
from augint_tools.team_secrets.repo import is_team_repo, pull_repo
from augint_tools.team_secrets.sops import decrypt_file, is_sops_installed


def run_checks(team: str) -> list[DoctorCheck]:
    """Run all health checks for a team configuration.

    Returns a list of DoctorCheck results.
    """
    checks: list[DoctorCheck] = []

    checks.append(_check_sops())
    checks.append(_check_age())
    checks.append(_check_config(team))

    config = load_team_config(team)
    repo_path = Path(config.repo_path) if config else None

    checks.append(_check_cached_key(team))
    checks.append(_check_key_permissions(team))
    checks.append(_check_repo_exists(team, repo_path))
    checks.append(_check_repo_pull(team, repo_path))
    checks.append(_check_decrypt(team, repo_path))
    checks.append(_check_gh_auth())

    return checks


def _check_sops() -> DoctorCheck:
    if is_sops_installed():
        return DoctorCheck("sops", "pass", "sops >= 3.8 found")
    # Check if installed but wrong version
    try:
        result = subprocess.run(["sops", "--version"], capture_output=True, text=True)
        if result.returncode == 0:
            return DoctorCheck(
                "sops", "fail", f"sops found but version too old: {result.stdout.strip()}"
            )
    except FileNotFoundError:
        pass
    return DoctorCheck("sops", "fail", "sops not found. Install: https://github.com/getsops/sops")


def _check_age() -> DoctorCheck:
    if is_age_installed():
        return DoctorCheck("age", "pass", "age and age-keygen found")
    return DoctorCheck("age", "fail", "age not found. Install: https://github.com/FiloSottile/age")


def _check_config(team: str) -> DoctorCheck:
    config = load_team_config(team)
    if config:
        return DoctorCheck("config", "pass", f"Team '{team}' configured: {config.repo_path}")
    return DoctorCheck(
        "config", "fail", f"No config for team '{team}'. Run: ai-tools team-secrets {team} setup"
    )


def _check_cached_key(team: str) -> DoctorCheck:
    key_path = get_cached_key(team)
    if key_path:
        return DoctorCheck("key_cached", "pass", f"Cached key at {key_path}")
    return DoctorCheck(
        "key_cached", "fail", f"No cached key. Run: ai-tools team-secrets {team} setup"
    )


def _check_key_permissions(team: str) -> DoctorCheck:
    key_path = get_cached_key(team)
    if key_path is None:
        return DoctorCheck("key_perms", "warn", "No key to check permissions on")
    if verify_key_permissions(key_path):
        return DoctorCheck("key_perms", "pass", "Key file permissions are 600")
    import sys

    if sys.platform == "win32":
        return DoctorCheck("key_perms", "warn", "Permission check skipped on Windows")
    return DoctorCheck("key_perms", "warn", f"Key file permissions are not 600: {key_path}")


def _check_repo_exists(team: str, repo_path: Path | None) -> DoctorCheck:
    if repo_path and repo_path.exists() and is_team_repo(repo_path):
        return DoctorCheck("repo", "pass", f"Team repo found at {repo_path}")
    if repo_path and repo_path.exists():
        return DoctorCheck(
            "repo", "warn", f"Directory exists but missing team repo structure: {repo_path}"
        )
    return DoctorCheck(
        "repo", "fail", f"Team repo not found. Run: ai-tools team-secrets {team} setup"
    )


def _check_repo_pull(team: str, repo_path: Path | None) -> DoctorCheck:
    if repo_path is None or not repo_path.exists():
        return DoctorCheck("repo_pull", "warn", "Skipped (no repo path)")

    if pull_repo(repo_path):
        return DoctorCheck("repo_pull", "pass", "Team repo pull succeeded")
    return DoctorCheck("repo_pull", "warn", "Team repo pull failed (no remote or network issue)")


def _check_decrypt(team: str, repo_path: Path | None) -> DoctorCheck:
    if repo_path is None or not repo_path.exists():
        return DoctorCheck("decrypt", "warn", "Skipped (no repo path)")

    key_path = get_cached_key(team)
    if key_path is None:
        return DoctorCheck("decrypt", "warn", "Skipped (no cached key)")

    # Find any .enc.env file to test
    enc_files = list(repo_path.glob("projects/**/*.enc.env"))
    if not enc_files:
        return DoctorCheck("decrypt", "warn", "No encrypted files found to test")

    # Try to decrypt the first one
    try:
        decrypt_file(enc_files[0], key_path)
        return DoctorCheck("decrypt", "pass", f"Successfully decrypted {enc_files[0].name}")
    except RuntimeError as e:
        return DoctorCheck("decrypt", "fail", f"Decryption failed: {e}")


def _check_gh_auth() -> DoctorCheck:
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return DoctorCheck("gh_auth", "pass", "GitHub CLI authenticated")
        return DoctorCheck(
            "gh_auth", "warn", "GitHub CLI not authenticated (sync to GitHub will fail)"
        )
    except FileNotFoundError:
        return DoctorCheck("gh_auth", "warn", "gh CLI not installed (sync to GitHub unavailable)")
