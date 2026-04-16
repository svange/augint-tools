"""Health check protocol and registry."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from github.Repository import Repository

    from .._data import RepoStatus
    from ._models import HealthCheckResult


@runtime_checkable
class HealthCheck(Protocol):
    """Interface every health check must implement."""

    name: str
    description: str

    def evaluate(
        self,
        repo: Repository,
        status: RepoStatus,
        *,
        config: dict,
        pulls: list | None = None,
    ) -> HealthCheckResult:
        """Run the check and return a result.

        Args:
            repo: PyGithub Repository object for API calls.
            status: Already-fetched RepoStatus (avoids re-fetching CI data).
            config: User-configurable thresholds.
            pulls: Pre-fetched open PRs list (shared across checks to save API calls).
        """
        ...


_CHECKS: dict[str, HealthCheck] = {}
_LOADED = False


def register(check: HealthCheck) -> HealthCheck:
    """Register a health check instance."""
    _CHECKS[check.name] = check
    return check


def get_check(name: str) -> HealthCheck:
    _ensure_builtins()
    return _CHECKS[name]


def available_checks() -> list[str]:
    _ensure_builtins()
    return sorted(_CHECKS)


def all_checks() -> list[HealthCheck]:
    _ensure_builtins()
    return list(_CHECKS.values())


def _ensure_builtins() -> None:
    global _LOADED  # noqa: PLW0603
    if _LOADED:
        return
    _LOADED = True
    # Import triggers check registration via module-level register() calls.
    import augint_tools.dashboard.health.checks  # noqa: F401
