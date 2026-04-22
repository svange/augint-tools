"""Built-in health checks. Importing this module triggers registration."""

from . import (
    broken_ci,
    coverage,
    open_issues,
    open_prs,
    renovate,
    renovate_prs,
    service_missing_dev,
    stale_prs,
    yaml_engine,
)

__all__ = [
    "broken_ci",
    "coverage",
    "open_issues",
    "open_prs",
    "renovate",
    "renovate_prs",
    "service_missing_dev",
    "stale_prs",
    "yaml_engine",
]
