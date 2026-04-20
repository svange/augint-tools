"""Create fine-grained GitHub personal access tokens.

Wraps the third-party ``github-fine-grained-token-client`` library, which
drives GitHub's web form over HTTP since no official API exists for
creating fine-grained PATs.
"""

from __future__ import annotations

import getpass
import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from github_fine_grained_token_client import (
    AccountPermission,
    BlockingPromptTwoFactorOtpProvider,
    GithubCredentials,
    LoginError,
    PasswordError,
    PermissionValue,
    RepositoryNotFoundError,
    RepositoryPermission,
    SelectRepositories,
    TokenCreationError,
    TokenNameAlreadyTakenError,
    TooManyAttemptsError,
    TwoFactorAuthenticationError,
    TwoFactorOtpProvider,
    UsernameError,
    async_client,
)
from github_fine_grained_token_client.permissions import permission_from_str

PermissionKey = AccountPermission | RepositoryPermission
PermissionMap = dict[PermissionKey, PermissionValue]


class PatCreationError(Exception):
    """User-facing error wrapping upstream library exceptions."""


@dataclass
class PatCredentials:
    username: str
    password: str


@dataclass
class PatRequest:
    name: str
    owner: str
    repo_names: list[str]
    permissions: PermissionMap
    expires_days: int = 30
    description: str = ""


def parse_permissions(spec: str) -> PermissionMap:
    """Parse ``"contents=write,metadata=read"`` into a permission map.

    Unknown permission keys or levels raise ``ValueError``.
    """
    if not spec or not spec.strip():
        raise ValueError("permissions spec is empty")

    result: PermissionMap = {}
    for raw_pair in spec.split(","):
        pair = raw_pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            raise ValueError(f"invalid permission spec {pair!r}: expected key=level")
        key, _, level = pair.partition("=")
        key = key.strip()
        level = level.strip().lower()
        try:
            perm = permission_from_str(key)
        except Exception as exc:
            raise ValueError(f"unknown permission key {key!r}") from exc
        try:
            pv = PermissionValue[level.upper()]
        except KeyError as exc:
            raise ValueError(
                f"invalid permission level {level!r} for {key!r}: expected none/read/write"
            ) from exc
        result[perm] = pv

    if not result:
        raise ValueError("no permissions parsed")
    return result


def parse_repo_specs(repo_specs: list[str]) -> tuple[str, list[str]]:
    """Parse ``["owner/repo1", "owner/repo2"]`` into ``("owner", ["repo1", "repo2"])``.

    Raises ``ValueError`` if any spec is malformed or if owners differ.
    """
    if not repo_specs:
        raise ValueError("at least one --repo is required")

    owners: set[str] = set()
    names: list[str] = []
    for spec in repo_specs:
        if "/" not in spec:
            raise ValueError(f"invalid repo spec {spec!r}: expected owner/repo")
        owner, _, name = spec.partition("/")
        owner = owner.strip()
        name = name.strip()
        if not owner or not name:
            raise ValueError(f"invalid repo spec {spec!r}: expected owner/repo")
        owners.add(owner)
        names.append(name)
    if len(owners) > 1:
        raise ValueError(f"all --repo values must share the same owner; got {sorted(owners)}")
    return next(iter(owners)), names


def resolve_credentials(
    username_env: str = "GITHUB_USERNAME",
    password_env: str = "GITHUB_PASSWORD",
    interactive: bool = True,
) -> PatCredentials:
    """Load credentials from env vars, prompting for anything missing.

    ``interactive=False`` disables prompts (for tests) and raises
    ``PatCreationError`` if either value is missing.
    """
    username = os.environ.get(username_env, "").strip()
    password = os.environ.get(password_env, "")

    if not username:
        if not interactive:
            raise PatCreationError(f"{username_env} is not set")
        username = input("GitHub username: ").strip()
    if not password:
        if not interactive:
            raise PatCreationError(f"{password_env} is not set")
        password = getpass.getpass("GitHub password: ")

    if not username or not password:
        raise PatCreationError("username and password are required")
    return PatCredentials(username=username, password=password)


async def create_pat(
    request: PatRequest,
    credentials: PatCredentials,
    otp_provider: TwoFactorOtpProvider | None = None,
) -> str:
    """Create a fine-grained PAT and return the plaintext token string."""
    gh_creds = GithubCredentials(username=credentials.username, password=credentials.password)
    provider = otp_provider or BlockingPromptTwoFactorOtpProvider()
    try:
        async with async_client(credentials=gh_creds, two_factor_otp_provider=provider) as session:
            return await session.create_token(
                name=request.name,
                expires=timedelta(days=request.expires_days),
                description=request.description,
                resource_owner=request.owner,
                scope=SelectRepositories(request.repo_names),
                permissions=request.permissions,
            )
    except TooManyAttemptsError as exc:
        raise PatCreationError(
            "GitHub rate-limited this account. Wait a few minutes before retrying."
        ) from exc
    except (LoginError, PasswordError, UsernameError) as exc:
        raise PatCreationError(f"authentication failed: {exc}") from exc
    except TwoFactorAuthenticationError as exc:
        raise PatCreationError(f"two-factor authentication failed: {exc}") from exc
    except RepositoryNotFoundError as exc:
        raise PatCreationError(f"repository not found: {exc}") from exc
    except TokenNameAlreadyTakenError as exc:
        raise PatCreationError(
            f"token name {request.name!r} is already taken. "
            "Choose a different --name or revoke the existing token."
        ) from exc
    except TokenCreationError as exc:
        raise PatCreationError(f"token creation failed: {exc}") from exc


def write_token_to_env(env_path: Path, var_name: str, token: str) -> None:
    """Set or update ``var_name=token`` in ``env_path``, creating the file if needed."""
    from dotenv import set_key

    env_path = Path(env_path)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    if not env_path.exists():
        env_path.touch()
    set_key(str(env_path), var_name, token, quote_mode="never")


__all__ = [
    "PatCreationError",
    "PatCredentials",
    "PatRequest",
    "PermissionKey",
    "PermissionMap",
    "create_pat",
    "parse_permissions",
    "parse_repo_specs",
    "resolve_credentials",
    "write_token_to_env",
]
