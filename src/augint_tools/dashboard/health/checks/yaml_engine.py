"""Health check that runs the YAML compliance engine.

This one check is intentionally different from the others: it returns a
**list** of ``HealthCheckResult`` objects, one per rule declared in
``standards.yaml``. The runner in ``health/__init__.py`` flattens the list so
every rule surfaces as its own finding on the repo card.

The engine fetches ``standards.yaml`` from ``augmenting-integrations/ai-cc-tools``
using the same auth token the dashboard's GraphQL queries use. Cache TTL is
one hour so edits propagate naturally without thrashing the GitHub API.

Results are cached per repo by ``(commit_sha, rulesets_fingerprint)``. When
neither the code nor the rulesets have changed, the cached results are
returned without re-evaluation. A ruleset change (detected via the REST
fetcher's ``updated_at`` tracking) invalidates the cache for that repo.

Runtime options (standards URL override, handler registry) come from
``config["standards_engine"]`` passed down from ``cmd.py``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .._engine import EngineOptions, run_engine
from .._handlers import all_handlers
from .._models import HealthCheckResult, Severity
from .._registry import register

if TYPE_CHECKING:
    from github.Repository import Repository

    from ..._data import RepoStatus
    from .. import FetchContext


def _rulesets_fingerprint(rulesets: list[dict] | None) -> str:
    """Stable string representation of rulesets for cache keying."""
    if not rulesets:
        return ""
    return json.dumps(rulesets, sort_keys=True, default=str)


class YamlEngineCheck:
    """Loads standards.yaml and emits one result per declared check.

    The engine is entirely offline-friendly: when the standards document can't
    be fetched and there's no cached copy, it emits a single informational
    result and the dashboard carries on.

    Results are cached by ``(commit_sha, rulesets_fingerprint)`` per repo.
    """

    name = "standards_engine"
    description = "Evaluate the canonical standards.yaml compliance rules"

    def __init__(self) -> None:
        # Cache: repo_full_name -> (cache_key, results)
        self._cache: dict[str, tuple[tuple[str | None, str], list[HealthCheckResult]]] = {}

    def evaluate(
        self,
        repo: Repository,  # noqa: ARG002 -- kept for Protocol signature
        status: RepoStatus,
        *,
        config: dict,
        context: FetchContext,
    ) -> list[HealthCheckResult]:
        engine_cfg = (config or {}).get("standards_engine") or {}
        gh = engine_cfg.get("gh")
        standards_url = engine_cfg.get("url")

        repo_key = getattr(status, "full_name", "") or ""
        sha = context.main_head_sha
        rulesets_fp = _rulesets_fingerprint(context.rulesets)
        cache_key = (sha, rulesets_fp)

        cached = self._cache.get(repo_key)
        if cached is not None and cached[0] == cache_key:
            return cached[1]

        options = EngineOptions(standards_url=standards_url, handlers=all_handlers())

        tags: set[str] = set()
        if getattr(status, "is_workspace", False):
            tags.add("workspace")
        elif getattr(status, "looks_like_service", False):
            tags.add("service")
        else:
            tags.add("library")
        if getattr(status, "is_org", False):
            tags.add("org")

        default_branch = getattr(status, "default_branch", None) or "main"
        try:
            results = run_engine(context, options, gh, tags, default_branch)
        except Exception as exc:
            results = [
                HealthCheckResult(
                    check_name=self.name,
                    severity=Severity.MEDIUM,
                    summary=f"standards engine error: {exc.__class__.__name__}: {exc}",
                )
            ]

        self._cache[repo_key] = (cache_key, results)
        return results


register(YamlEngineCheck())
