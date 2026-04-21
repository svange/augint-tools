"""Health checks for team secrets configuration."""

from __future__ import annotations

import subprocess

from augint_tools.team_secrets.age import is_age_installed
from augint_tools.team_secrets.checkout import secrets_repo_slug
from augint_tools.team_secrets.keys import (
    detect_project_name,
    get_cached_key,
    load_team_config,
    verify_key_permissions,
)
from augint_tools.team_secrets.models import DoctorCheck
from augint_tools.team_secrets.sops import is_sops_installed


def run_checks(team: str, org: str) -> list[DoctorCheck]:
    """Run all health checks for a team configuration."""
    checks: list[DoctorCheck] = []

    checks.append(_check_sops())
    checks.append(_check_age())
    checks.append(_check_config(team))
    checks.append(_check_cached_key(team))
    checks.append(_check_key_permissions(team))
    checks.append(_check_gh_auth())
    checks.append(_check_secrets_repo_access(team, org))
    checks.append(_check_project_detection())

    return checks


def _check_sops() -> DoctorCheck:
    if is_sops_installed():
        return DoctorCheck("sops", "pass", "sops >= 3.8 found")
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
        return DoctorCheck("config", "pass", f"Team '{team}' configured (org: {config.org})")
    return DoctorCheck(
        "config",
        "warn",
        f"No config for team '{team}'. Run: ai-tools team-secrets {team} setup",
    )


def _check_cached_key(team: str) -> DoctorCheck:
    key_path = get_cached_key(team)
    if key_path:
        return DoctorCheck("key_cached", "pass", f"Cached key at {key_path}")
    return DoctorCheck(
        "key_cached",
        "fail",
        f"No cached key. Run: ai-tools team-secrets {team} setup",
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
            "gh_auth", "warn", "GitHub CLI not authenticated (team-secrets requires gh auth)"
        )
    except FileNotFoundError:
        return DoctorCheck("gh_auth", "fail", "gh CLI not installed (required for team-secrets)")


def _check_secrets_repo_access(team: str, org: str) -> DoctorCheck:
    slug = secrets_repo_slug(team, org)
    try:
        result = subprocess.run(
            ["gh", "repo", "view", slug, "--json", "name"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return DoctorCheck("secrets_repo", "pass", f"Can access {slug}")
        return DoctorCheck("secrets_repo", "fail", f"Cannot access {slug}: check permissions")
    except FileNotFoundError:
        return DoctorCheck("secrets_repo", "warn", "gh CLI not available to check repo access")


def _check_project_detection() -> DoctorCheck:
    project = detect_project_name()
    if project:
        return DoctorCheck("project_detect", "pass", f"Detected project: {project}")
    return DoctorCheck(
        "project_detect", "warn", "Not in a git repo (project auto-detection unavailable)"
    )
