"""Tests for the REST rulesets fetcher and format adapter."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from augint_tools.dashboard._rulesets import RulesetFetcher, _rest_to_graphql_format


class TestRestToGraphqlFormat:
    """Verify the REST-to-GraphQL adapter produces the shape the engine expects."""

    def test_uppercases_target(self):
        rest = {"target": "branch", "rules": [], "bypass_actors": []}
        result = _rest_to_graphql_format(rest)
        assert result["target"] == "BRANCH"

    def test_uppercases_rule_type(self):
        rest = {
            "target": "branch",
            "rules": [{"type": "required_status_checks", "parameters": {}}],
            "bypass_actors": [],
        }
        result = _rest_to_graphql_format(rest)
        assert result["rules"]["nodes"][0]["type"] == "REQUIRED_STATUS_CHECKS"

    def test_wraps_rules_in_nodes(self):
        rest = {
            "target": "branch",
            "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}],
            "bypass_actors": [],
        }
        result = _rest_to_graphql_format(rest)
        assert "nodes" in result["rules"]
        assert len(result["rules"]["nodes"]) == 2

    def test_wraps_bypass_actors_in_nodes(self):
        rest = {
            "target": "branch",
            "rules": [],
            "bypass_actors": [
                {"actor_type": "DeployKey", "actor_id": None, "bypass_mode": "always"},
            ],
        }
        result = _rest_to_graphql_format(rest)
        nodes = result["bypassActors"]["nodes"]
        assert len(nodes) == 1
        assert nodes[0]["actorType"] == "DeployKey"
        assert nodes[0]["bypassMode"] == "always"

    def test_preserves_parameters_as_dict(self):
        params = {"required_status_checks": [{"context": "Unit tests"}]}
        rest = {
            "target": "branch",
            "rules": [{"type": "required_status_checks", "parameters": params}],
            "bypass_actors": [],
        }
        result = _rest_to_graphql_format(rest)
        assert result["rules"]["nodes"][0]["parameters"] == params

    def test_preserves_name_and_enforcement(self):
        rest = {
            "name": "library",
            "target": "branch",
            "enforcement": "active",
            "rules": [],
            "bypass_actors": [],
        }
        result = _rest_to_graphql_format(rest)
        assert result["name"] == "library"
        assert result["enforcement"] == "ACTIVE"

    def test_handles_empty_rules_and_bypass(self):
        rest = {"target": "tag", "rules": [], "bypass_actors": []}
        result = _rest_to_graphql_format(rest)
        assert result["rules"]["nodes"] == []
        assert result["bypassActors"]["nodes"] == []

    def test_handles_missing_fields_gracefully(self):
        rest = {}
        result = _rest_to_graphql_format(rest)
        assert result["target"] == ""
        assert result["rules"]["nodes"] == []
        assert result["bypassActors"]["nodes"] == []


def _mock_requester(responses: dict[str, tuple[dict[str, Any], Any]]) -> MagicMock:
    """Build a mock Github whose requester returns path-keyed responses."""
    gh = MagicMock()
    requester = MagicMock()

    def side_effect(method: str, path: str, *args: Any, **kwargs: Any) -> Any:
        for prefix, resp in responses.items():
            if path.rstrip("/").endswith(prefix.rstrip("/")):
                return resp
        raise ValueError(f"unexpected path: {path}")

    requester.requestJsonAndCheck.side_effect = side_effect
    gh.requester = requester
    return gh


# Minimal REST responses for a repo with one ruleset.
_LIST_RESPONSE: list[dict[str, Any]] = [
    {
        "id": 100,
        "name": "main-protection",
        "target": "branch",
        "source_type": "Repository",
        "source": "org/repo",
        "enforcement": "active",
        "updated_at": "2026-04-20T00:00:00Z",
    },
]

_DETAIL_RESPONSE: dict[str, Any] = {
    "id": 100,
    "name": "main-protection",
    "target": "branch",
    "enforcement": "active",
    "rules": [
        {
            "type": "required_status_checks",
            "parameters": {
                "required_status_checks": [{"context": "CI"}],
            },
        },
    ],
    "bypass_actors": [
        {"actor_type": "RepositoryRole", "actor_id": 4, "bypass_mode": "always"},
    ],
    "updated_at": "2026-04-20T00:00:00Z",
}


class TestRulesetFetcher:
    """Verify RulesetFetcher caching and REST call behaviour."""

    def test_cold_start_fetches_list_and_detail(self) -> None:
        gh = _mock_requester(
            {
                "/repos/org/repo/rulesets": ({}, _LIST_RESPONSE),
                "/repos/org/repo/rulesets/100": ({}, _DETAIL_RESPONSE),
            }
        )
        fetcher = RulesetFetcher()
        result = fetcher.fetch("org/repo", gh)
        assert len(result) == 1
        assert result[0]["target"] == "BRANCH"
        assert result[0]["rules"]["nodes"][0]["type"] == "REQUIRED_STATUS_CHECKS"

    def test_cached_skips_detail_call(self) -> None:
        gh = _mock_requester(
            {
                "/repos/org/repo/rulesets": ({}, _LIST_RESPONSE),
                "/repos/org/repo/rulesets/100": ({}, _DETAIL_RESPONSE),
            }
        )
        fetcher = RulesetFetcher()
        fetcher.fetch("org/repo", gh)

        # Second fetch -- same updated_at, should skip detail call.
        call_count_before = gh.requester.requestJsonAndCheck.call_count
        result = fetcher.fetch("org/repo", gh)
        call_count_after = gh.requester.requestJsonAndCheck.call_count
        # Only the list call should have fired (1 new call, not 2).
        assert call_count_after - call_count_before == 1
        assert len(result) == 1

    def test_updated_at_change_triggers_detail_refetch(self) -> None:
        updated_list = [{**_LIST_RESPONSE[0], "updated_at": "2026-04-21T00:00:00Z"}]
        updated_detail = {**_DETAIL_RESPONSE, "updated_at": "2026-04-21T00:00:00Z"}
        gh = _mock_requester(
            {
                "/repos/org/repo/rulesets": ({}, _LIST_RESPONSE),
                "/repos/org/repo/rulesets/100": ({}, _DETAIL_RESPONSE),
            }
        )
        fetcher = RulesetFetcher()
        fetcher.fetch("org/repo", gh)

        # Simulate updated_at change.
        gh2 = _mock_requester(
            {
                "/repos/org/repo/rulesets": ({}, updated_list),
                "/repos/org/repo/rulesets/100": ({}, updated_detail),
            }
        )
        result = fetcher.fetch("org/repo", gh2)
        assert len(result) == 1

    def test_new_ruleset_triggers_detail_fetch(self) -> None:
        new_rs: dict[str, Any] = {
            "id": 200,
            "name": "new-rule",
            "target": "branch",
            "source_type": "Repository",
            "source": "org/repo",
            "enforcement": "active",
            "updated_at": "2026-04-21T00:00:00Z",
        }
        new_detail: dict[str, Any] = {
            "id": 200,
            "name": "new-rule",
            "target": "branch",
            "enforcement": "active",
            "rules": [],
            "bypass_actors": [],
            "updated_at": "2026-04-21T00:00:00Z",
        }
        gh = _mock_requester(
            {
                "/repos/org/repo/rulesets": ({}, _LIST_RESPONSE),
                "/repos/org/repo/rulesets/100": ({}, _DETAIL_RESPONSE),
            }
        )
        fetcher = RulesetFetcher()
        fetcher.fetch("org/repo", gh)

        gh2 = _mock_requester(
            {
                "/repos/org/repo/rulesets": ({}, _LIST_RESPONSE + [new_rs]),
                "/repos/org/repo/rulesets/100": ({}, _DETAIL_RESPONSE),
                "/repos/org/repo/rulesets/200": ({}, new_detail),
            }
        )
        result = fetcher.fetch("org/repo", gh2)
        assert len(result) == 2

    def test_removed_ruleset_drops_from_cache(self) -> None:
        gh = _mock_requester(
            {
                "/repos/org/repo/rulesets": ({}, _LIST_RESPONSE),
                "/repos/org/repo/rulesets/100": ({}, _DETAIL_RESPONSE),
            }
        )
        fetcher = RulesetFetcher()
        fetcher.fetch("org/repo", gh)

        gh2 = _mock_requester(
            {
                "/repos/org/repo/rulesets": ({}, []),
            }
        )
        result = fetcher.fetch("org/repo", gh2)
        assert result == []

    def test_api_error_returns_cached(self) -> None:
        gh = _mock_requester(
            {
                "/repos/org/repo/rulesets": ({}, _LIST_RESPONSE),
                "/repos/org/repo/rulesets/100": ({}, _DETAIL_RESPONSE),
            }
        )
        fetcher = RulesetFetcher()
        fetcher.fetch("org/repo", gh)

        gh_err = MagicMock()
        gh_err.requester.requestJsonAndCheck.side_effect = Exception("network")
        result = fetcher.fetch("org/repo", gh_err)
        assert len(result) == 1

    def test_api_error_cold_start_returns_empty(self) -> None:
        gh = MagicMock()
        gh.requester.requestJsonAndCheck.side_effect = Exception("network")
        fetcher = RulesetFetcher()
        result = fetcher.fetch("org/repo", gh)
        assert result == []
