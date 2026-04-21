"""Data models for team secrets management."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class TeamConfig:
    """Persisted team configuration from ~/.augint-tools/teams.yaml."""

    name: str
    repo_path: Path
    username: str


@dataclass(frozen=True)
class ProjectConfig:
    """Metadata for a project within a team secrets repo."""

    name: str
    repo: str  # owner/repo format
    description: str
    environments: list[str] = field(default_factory=lambda: ["dev", "prod"])


@dataclass(frozen=True)
class UserRecord:
    """A team member's identity for recipient management."""

    name: str
    public_key: str


@dataclass(frozen=True)
class ConflictEntry:
    """A single key with conflicting values between local and team."""

    key: str
    local_value: str
    team_value: str


@dataclass(frozen=True)
class MergeResult:
    """Result of merging local .env with team encrypted file."""

    merged: dict[str, str]
    additions: list[str]  # Keys only in local (added to team)
    conflicts: list[ConflictEntry]  # Keys with differing values
    unchanged: list[str]  # Keys with same value in both


@dataclass(frozen=True)
class DoctorCheck:
    """Result of a single health check."""

    name: str
    status: str  # "pass", "warn", "fail"
    message: str
