"""AWS profile probing module for SSO session status.

Pure data layer -- no UI imports. Thread-safe for use from Textual worker threads.
"""

from __future__ import annotations

import configparser
import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

_PROFILE_NAME_RE = re.compile(r"^[a-zA-Z0-9._-]+$")

_CACHE_DIR = Path.home() / ".cache" / "ai-gh"
_CACHE_FILE = _CACHE_DIR / "aws_cache.json"


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
    """Extract SSO and region fields from the parsed config for a profile.

    Resolves ``sso_session`` indirection used by newer AWS CLI v2 configs,
    where the start URL lives in a ``[sso-session NAME]`` section.
    """
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
        if result["sso_start_url"] is None:
            sso_session = config.get(section, "sso_session", fallback=None)
            if sso_session:
                session_section = f"sso-session {sso_session}"
                if config.has_section(session_section):
                    result["sso_start_url"] = config.get(
                        session_section, "sso_start_url", fallback=None
                    )
    return result


def _load_sso_token_cache() -> dict[str, str]:
    """Return ``startUrl -> expiresAt`` from ``~/.aws/sso/cache``.

    Used to short-circuit the sts round-trip for profiles whose SSO token
    is already expired locally. The expiresAt is kept as the raw string
    from AWS so parsing errors stay isolated to :func:`_sso_token_expired`.
    """
    cache_dir = Path.home() / ".aws" / "sso" / "cache"
    result: dict[str, str] = {}
    if not cache_dir.is_dir():
        return result
    for path in cache_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        start_url = data.get("startUrl")
        expires_at = data.get("expiresAt")
        if isinstance(start_url, str) and isinstance(expires_at, str):
            result[start_url] = expires_at
    return result


def _sso_token_expired(expires_at_iso: str) -> bool:
    """Return True if the ISO timestamp is already in the past (or unparseable)."""
    try:
        s = expires_at_iso.replace("UTC", "+00:00").replace("Z", "+00:00")
        expires = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return True
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return expires <= datetime.now(UTC)


def _make_profile(
    name: str, cfg: dict[str, str | None], status: str, error: str | None
) -> AwsProfile:
    return AwsProfile(
        name=name,
        region=cfg["region"],
        sso_start_url=cfg["sso_start_url"],
        sso_account_id=cfg["sso_account_id"],
        sso_role_name=cfg["sso_role_name"],
        account_id=None,
        user_arn=None,
        status=status,
        error=error,
    )


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


def probe_aws_local(previous_state: AwsState | None = None) -> AwsState:
    """Resolve AWS profile statuses purely from on-disk state.

    Fires zero subprocesses, so this finishes in single-digit milliseconds
    regardless of profile count. Status is derived entirely from
    ``~/.aws/config`` and ``~/.aws/sso/cache``:

    * SSO profile + no local token        -> ``expired``
    * SSO profile + token past expiresAt  -> ``expired``
    * SSO profile + token still valid     -> ``active``
    * Non-SSO profile                     -> carry previous status forward,
                                             or ``unknown`` if we've never
                                             successfully verified it

    ``account_id`` / ``user_arn`` are carried forward from *previous_state*
    when available (they can only be populated by a prior sts call, but
    they rarely change so cached values are fine for display).
    """
    cli_available = _is_aws_cli_available()
    profile_names = list_aws_profiles()

    config_path = _aws_config_path()
    config = configparser.ConfigParser()
    try:
        config.read(str(config_path))
    except configparser.Error:
        pass

    sso_tokens = _load_sso_token_cache() if cli_available else {}
    previous_map: dict[str, AwsProfile] = {}
    if previous_state is not None:
        previous_map = {p.name: p for p in previous_state.profiles}

    results: list[AwsProfile] = []
    for name in profile_names:
        cfg = _parse_config_for_profile(config, name)
        prev = previous_map.get(name)

        if not cli_available:
            results.append(_make_profile(name, cfg, "unknown", "aws CLI not found"))
            continue

        sso_url = cfg["sso_start_url"]
        if sso_url is None:
            # Non-SSO profile: local state can't tell us anything. Preserve
            # the previous probe's result so the drawer doesn't regress to
            # "unknown" just because we stopped hitting sts.
            if prev is not None:
                results.append(prev)
            else:
                results.append(_make_profile(name, cfg, "unknown", "not verified"))
            continue

        token_expiry = sso_tokens.get(sso_url)
        if token_expiry is None:
            results.append(_make_profile(name, cfg, "expired", "no SSO token cached"))
            continue
        if _sso_token_expired(token_expiry):
            results.append(_make_profile(name, cfg, "expired", "SSO token expired"))
            continue

        # Token is valid. Carry forward any previously verified identity
        # fields so the drawer can still show account/arn next to the name.
        account_id = prev.account_id if prev is not None else None
        user_arn = prev.user_arn if prev is not None else None
        results.append(
            AwsProfile(
                name=name,
                region=cfg["region"],
                sso_start_url=sso_url,
                sso_account_id=cfg["sso_account_id"],
                sso_role_name=cfg["sso_role_name"],
                account_id=account_id,
                user_arn=user_arn,
                status="active",
                error=None,
            )
        )

    return AwsState(
        profiles=tuple(results),
        aws_cli_available=cli_available,
        last_check_at=datetime.now(UTC).isoformat(),
    )


def save_aws_cache(state: AwsState) -> None:
    """Persist AwsState to disk so the drawer paints instantly on next start."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "profiles": [asdict(p) for p in state.profiles],
        "aws_cli_available": state.aws_cli_available,
        "last_check_at": state.last_check_at,
    }
    _CACHE_FILE.write_text(json.dumps(data, indent=2))


def load_aws_cache() -> AwsState | None:
    """Return the last persisted AwsState, or ``None`` if unavailable."""
    if not _CACHE_FILE.exists():
        return None
    try:
        data = json.loads(_CACHE_FILE.read_text())
        profiles = tuple(AwsProfile(**p) for p in data.get("profiles", []))
        return AwsState(
            profiles=profiles,
            aws_cli_available=bool(data.get("aws_cli_available", False)),
            last_check_at=data.get("last_check_at"),
        )
    except (json.JSONDecodeError, TypeError, KeyError):
        return None


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
