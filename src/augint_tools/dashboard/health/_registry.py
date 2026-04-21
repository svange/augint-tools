"""Health check protocol and registry."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from github.Repository import Repository

    from .._data import RepoStatus
    from . import FetchContext
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
        context: FetchContext,
    ) -> HealthCheckResult:
        """Run the check and return a result.

        Args:
            repo: PyGithub Repository object. Retained for the narrow set of
                checks that still need a live reference (none do after the
                GraphQL migration, but the signature stays stable so future
                checks that genuinely need REST access can use it).
            status: Already-fetched RepoStatus.
            config: User-configurable thresholds.
            context: FetchContext with pre-fetched data (pulls, issues,
                renovate config text, pipeline workflow text). Populated by
                the workspace GraphQL query. Checks must never make their
                own per-repo REST call.
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
