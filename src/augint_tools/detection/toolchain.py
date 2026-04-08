"""Toolchain detection from filesystem and available commands."""

import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ToolchainInfo:
    """Detected local toolchain availability."""

    package_manager: str | None = None  # uv, pip, npm, yarn, pnpm
    has_pre_commit: bool = False
    has_pytest: bool = False
    has_ruff: bool = False
    has_mypy: bool = False
    has_bandit: bool = False
    has_pip_audit: bool = False
    has_pip_licenses: bool = False
    has_npm: bool = False
    has_biome: bool = False


def detect_toolchain(path: Path) -> ToolchainInfo:
    """Detect available toolchain from filesystem markers and PATH.

    Checks both config files in the repo and available CLI tools.
    """
    info = ToolchainInfo()

    # Package manager detection (lock files first, then PATH fallback)
    if (path / "uv.lock").exists():
        info.package_manager = "uv"
    elif (path / "yarn.lock").exists():
        info.package_manager = "yarn"
    elif (path / "pnpm-lock.yaml").exists():
        info.package_manager = "pnpm"
    elif (path / "package-lock.json").exists():
        info.package_manager = "npm"
    elif (path / "Pipfile.lock").exists():
        info.package_manager = "pip"
    elif shutil.which("uv"):
        info.package_manager = "uv"

    # Python tooling (from config files)
    info.has_pre_commit = (path / ".pre-commit-config.yaml").exists()
    info.has_pytest = (
        (path / "pytest.ini").exists()
        or (path / "pyproject.toml").exists()  # pytest is configured via pyproject.toml
    )
    info.has_ruff = (path / "pyproject.toml").exists() or (path / "ruff.toml").exists()
    info.has_mypy = (path / "pyproject.toml").exists() or (path / "mypy.ini").exists()

    # Security/license tools (check PATH)
    info.has_bandit = shutil.which("bandit") is not None
    info.has_pip_audit = shutil.which("pip-audit") is not None
    info.has_pip_licenses = shutil.which("pip-licenses") is not None

    # JS/TS tooling
    info.has_npm = shutil.which("npm") is not None
    info.has_biome = (path / "biome.json").exists() or (path / "biome.jsonc").exists()

    return info
