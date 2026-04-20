"""AWS profile probing module for SSO session status.

Pure data layer -- no UI imports. Thread-safe for use from Textual worker threads.
"""

from __future__ import annotations

import configparser
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_PROFILE_NAME_RE = re.compile(r"^[a-zA-Z0-9._-]+$")


@dataclass(frozen=True)
class AwsProfile:
    """Represents a single AWS CLI profile and its session state."""

    name: str
    region: str | None
    sso_start_url: str | None
    sso_account_id: str | None
    sso_role_name: str | None
    account_id: str | None
    user_arn: str | None
    status: str  # "active", "expired", "error", "unknown"
    error: str | None


@dataclass(frozen=True)
class AwsState:
    """Aggregate state of all probed AWS profiles."""

    profiles: tuple[AwsProfile, ...]
    aws_cli_available: bool
    last_check_at: str | None  # ISO timestamp


def _aws_config_path() -> Path:
    """Return the path to ~/.aws/config."""
    return Path.home() / ".aws" / "config"


def _is_aws_cli_available() -> bool:
    """Check whether the aws CLI binary is on PATH."""
    return shutil.which("aws") is not None


def _is_safe_profile_name(name: str) -> bool:
    """Validate a profile name contains only shell-safe characters."""
    return bool(_PROFILE_NAME_RE.match(name))


def _parse_config_for_profile(
    config: configparser.ConfigParser, profile_name: str
) -> dict[str, str | None]:
    """Extract SSO and region fields from the parsed config for a profile."""
    section = "default" if profile_name == "default" else f"profile {profile_name}"
    result: dict[str, str | None] = {
        "region": None,
        "sso_start_url": None,
        "sso_account_id": None,
        "sso_role_name": None,
    }
    if config.has_section(section):
        result["region"] = config.get(section, "region", fallback=None)
        result["sso_start_url"] = config.get(section, "sso_start_url", fallback=None)
        result["sso_account_id"] = config.get(section, "sso_account_id", fallback=None)
        result["sso_role_name"] = config.get(section, "sso_role_name", fallback=None)
    return result


def list_aws_profiles() -> list[str]:
    """Parse ~/.aws/config and return a sorted list of profile names.

    Returns an empty list if the config file does not exist or cannot be parsed.
    """
    config_path = _aws_config_path()
    if not config_path.exists():
        return []

    config = configparser.ConfigParser()
    try:
        config.read(str(config_path))
    except configparser.Error:
        return []

    profiles: list[str] = []
    for section in config.sections():
        if section == "default":
            profiles.append("default")
        elif section.startswith("profile "):
            name = section[len("profile ") :]
            if name:
                profiles.append(name)

    return sorted(profiles)


def check_profile_status(profile_name: str) -> AwsProfile:
    """Check the session status of a single AWS profile.

    Runs ``aws sts get-caller-identity`` with a 5-second timeout.
    """
    # Read config metadata regardless of CLI availability
    config_path = _aws_config_path()
    config = configparser.ConfigParser()
    try:
        config.read(str(config_path))
    except configparser.Error:
        pass
    cfg = _parse_config_for_profile(config, profile_name)

    # Validate profile name before passing to subprocess
    if not _is_safe_profile_name(profile_name):
        return AwsProfile(
            name=profile_name,
            region=cfg["region"],
            sso_start_url=cfg["sso_start_url"],
            sso_account_id=cfg["sso_account_id"],
            sso_role_name=cfg["sso_role_name"],
            account_id=None,
            user_arn=None,
            status="error",
            error=f"Invalid profile name: {profile_name}",
        )

    if not _is_aws_cli_available():
        return AwsProfile(
            name=profile_name,
            region=cfg["region"],
            sso_start_url=cfg["sso_start_url"],
            sso_account_id=cfg["sso_account_id"],
            sso_role_name=cfg["sso_role_name"],
            account_id=None,
            user_arn=None,
            status="unknown",
            error="aws CLI not found",
        )

    try:
        result = subprocess.run(  # noqa: S603
            [
                "aws",
                "sts",
                "get-caller-identity",
                "--profile",
                profile_name,
                "--output",
                "json",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        return AwsProfile(
            name=profile_name,
            region=cfg["region"],
            sso_start_url=cfg["sso_start_url"],
            sso_account_id=cfg["sso_account_id"],
            sso_role_name=cfg["sso_role_name"],
            account_id=None,
            user_arn=None,
            status="error",
            error="Timed out after 5s",
        )

    if result.returncode == 0:
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return AwsProfile(
                name=profile_name,
                region=cfg["region"],
                sso_start_url=cfg["sso_start_url"],
                sso_account_id=cfg["sso_account_id"],
                sso_role_name=cfg["sso_role_name"],
                account_id=None,
                user_arn=None,
                status="error",
                error="Failed to parse sts response",
            )
        return AwsProfile(
            name=profile_name,
            region=cfg["region"],
            sso_start_url=cfg["sso_start_url"],
            sso_account_id=cfg["sso_account_id"],
            sso_role_name=cfg["sso_role_name"],
            account_id=data.get("Account"),
            user_arn=data.get("Arn"),
            status="active",
            error=None,
        )

    # Determine whether it's an expired token or some other error
    stderr = result.stderr or ""
    if "expired" in stderr.lower() or "sso session" in stderr.lower():
        status = "expired"
    else:
        status = "error"

    return AwsProfile(
        name=profile_name,
        region=cfg["region"],
        sso_start_url=cfg["sso_start_url"],
        sso_account_id=cfg["sso_account_id"],
        sso_role_name=cfg["sso_role_name"],
        account_id=None,
        user_arn=None,
        status=status,
        error=stderr.strip() or "Unknown error",
    )


def probe_aws(profiles: list[str] | None = None) -> AwsState:
    """Probe AWS profiles and return aggregate state.

    If *profiles* is None, discovers them via :func:`list_aws_profiles`.
    """
    cli_available = _is_aws_cli_available()

    if profiles is None:
        profiles = list_aws_profiles()

    if not cli_available:
        # Still parse config metadata but mark everything unknown
        config_path = _aws_config_path()
        config = configparser.ConfigParser()
        try:
            config.read(str(config_path))
        except configparser.Error:
            pass

        aws_profiles: list[AwsProfile] = []
        for name in profiles:
            cfg = _parse_config_for_profile(config, name)
            aws_profiles.append(
                AwsProfile(
                    name=name,
                    region=cfg["region"],
                    sso_start_url=cfg["sso_start_url"],
                    sso_account_id=cfg["sso_account_id"],
                    sso_role_name=cfg["sso_role_name"],
                    account_id=None,
                    user_arn=None,
                    status="unknown",
                    error="aws CLI not found",
                )
            )
        return AwsState(
            profiles=tuple(aws_profiles),
            aws_cli_available=False,
            last_check_at=datetime.now(UTC).isoformat(),
        )

    checked = [check_profile_status(name) for name in profiles]
    return AwsState(
        profiles=tuple(checked),
        aws_cli_available=True,
        last_check_at=datetime.now(UTC).isoformat(),
    )


def launch_sso_login(profile_name: str) -> bool:
    """Launch ``aws sso login`` as a non-blocking subprocess.

    Returns True if the process was launched, False if the aws CLI is not
    available or the profile name is invalid.
    """
    if not _is_safe_profile_name(profile_name):
        return False

    if not _is_aws_cli_available():
        return False

    subprocess.Popen(  # noqa: S603
        ["aws", "sso", "login", "--profile", profile_name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return True
