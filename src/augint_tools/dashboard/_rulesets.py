"""REST-based ruleset fetcher with updated_at caching.

Replaces the GraphQL ``rulesets`` field on the dashboard fragment, moving
ruleset fetching to the REST API which draws from a separate rate-limit
pool (5,000 req/hour) than GraphQL (5,000 points/hour). This frees ~15%
of the GraphQL budget for IDE plugins and other tools.

The list endpoint is called every refresh cycle (1 call/repo). Detail
endpoints are only called when a ruleset's ``updated_at`` changes, so
steady-state cost is minimal.

REST responses are transformed to match the GraphQL shape so the standards
engine and all consuming code need no changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from github import Github


def _rest_to_graphql_format(rest: dict[str, Any]) -> dict[str, Any]:
    """Transform a REST ruleset detail response to GraphQL-compatible shape.

    The engine's ``_check_ruleset_has_required_checks`` expects:
    - ``target``: uppercase (``"BRANCH"``)
    - ``rules.nodes[].type``: uppercase (``"REQUIRED_STATUS_CHECKS"``)
    - ``rules.nodes[].parameters``: dict
    - ``bypassActors.nodes[].actorType``: camelCase
    """
    rules = rest.get("rules") or []
    bypass_actors = rest.get("bypass_actors") or []
    return {
        "name": rest.get("name"),
        "target": (rest.get("target") or "").upper(),
        "enforcement": (rest.get("enforcement") or "").upper(),
        "rules": {
            "nodes": [
                {
                    "type": (r.get("type") or "").upper(),
                    "parameters": r.get("parameters"),
                }
                for r in rules
                if isinstance(r, dict)
            ],
        },
        "bypassActors": {
            "nodes": [
                {
                    "actorType": ba.get("actor_type"),
                    "actorId": ba.get("actor_id"),
                    "bypassMode": ba.get("bypass_mode"),
                }
                for ba in bypass_actors
                if isinstance(ba, dict)
            ],
        },
    }


@dataclass
class _CachedRuleset:
    """One ruleset's detail response, cached until updated_at changes."""

    updated_at: str
    graphql_format: dict[str, Any]


@dataclass
class _RepoCache:
    """Per-repo cache: the last-seen list of rulesets + detail data."""

    rulesets: dict[int, _CachedRuleset] = field(default_factory=dict)
    # The assembled list returned to callers, rebuilt when any entry changes.
    assembled: list[dict[str, Any]] = field(default_factory=list)


class RulesetFetcher:
    """Fetch rulesets via REST with updated_at-based caching.

    Call ``fetch(repo_full_name, gh)`` once per repo per refresh cycle.
    The list endpoint fires every call (1 REST point). Detail endpoints
    only fire when ``updated_at`` changes or for newly-seen ruleset IDs.

    On network error, returns the last cached value (or empty list on
    cold-start failure).
    """

    def __init__(self) -> None:
        self._cache: dict[str, _RepoCache] = {}

    def fetch(self, repo_full_name: str, gh: Github) -> list[dict[str, Any]]:
        """Return rulesets for one repo in GraphQL-compatible format."""
        try:
            return self._fetch_inner(repo_full_name, gh)
        except Exception as exc:
            logger.warning(
                "rulesets: {} fetch failed ({}: {}); using cache",
                repo_full_name,
                exc.__class__.__name__,
                exc,
            )
            cached = self._cache.get(repo_full_name)
            return list(cached.assembled) if cached else []

    def _fetch_inner(self, repo_full_name: str, gh: Github) -> list[dict[str, Any]]:
        requester = gh.requester  # type: ignore[union-attr]
        path = f"/repos/{repo_full_name}/rulesets"
        _headers, listing = requester.requestJsonAndCheck("GET", path)
        if not isinstance(listing, list):
            listing = []

        repo_cache = self._cache.setdefault(repo_full_name, _RepoCache())
        live_ids: set[int] = set()
        changed = False

        for entry in listing:
            if not isinstance(entry, dict):
                continue
            rs_id = entry.get("id")
            if not isinstance(rs_id, int):
                continue
            live_ids.add(rs_id)
            updated_at = entry.get("updated_at") or ""
            cached_rs = repo_cache.rulesets.get(rs_id)
            if cached_rs is not None and cached_rs.updated_at == updated_at:
                continue
            # Fetch detail for new or changed rulesets.
            _h, detail = requester.requestJsonAndCheck("GET", f"{path}/{rs_id}")
            if not isinstance(detail, dict):
                continue
            repo_cache.rulesets[rs_id] = _CachedRuleset(
                updated_at=updated_at,
                graphql_format=_rest_to_graphql_format(detail),
            )
            changed = True

        # Prune removed rulesets.
        stale = set(repo_cache.rulesets) - live_ids
        for rs_id in stale:
            del repo_cache.rulesets[rs_id]
            changed = True

        if changed or not repo_cache.assembled:
            repo_cache.assembled = [rs.graphql_format for rs in repo_cache.rulesets.values()]

        return list(repo_cache.assembled)

    def clear(self) -> None:
        """Drop all cached data. Intended for tests."""
        self._cache.clear()
