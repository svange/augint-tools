"""Tests for the batched GraphQL workspace fetcher."""

from __future__ import annotations

from unittest.mock import MagicMock

from augint_tools.dashboard._gql import (
    PIPELINE_PATHS,
    RENOVATE_PATHS,
    RepoSnapshot,
    build_query,
    build_teams_query,
    fetch_workspace_snapshot,
    fetch_workspace_teams,
    parse_response,
    parse_teams_response,
    pick_pipeline_yaml,
    pick_renovate_config,
    translate_rollup_state,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_repo(full_name: str):
    repo = MagicMock()
    repo.full_name = full_name
    return repo


def _graphql_repo_payload(
    *,
    full_name: str,
    has_dev: bool = False,
    main_state: str = "SUCCESS",
    main_history_states: list[str | None] | None = None,
    dev_state: str | None = None,
    dev_history_states: list[str | None] | None = None,
    pr_nodes: list[dict] | None = None,
    pr_total: int | None = None,
    issue_nodes: list[dict] | None = None,
    issue_total: int | None = None,
    renovate_hits: dict[str, str] | None = None,
    pipeline_hits: dict[str, str] | None = None,
    root_entries: list[str] | None = None,
    primary_language: str | None = None,
    is_private: bool = False,
) -> dict:
    """Build the per-repo shape that GraphQL would return for RepoFields."""
    owner, name = full_name.split("/", 1)

    def _history(states: list[str | None] | None) -> dict:
        # Default: history mirrors the tip rollup so callers that don't care
        # about walkback behavior keep working unchanged.
        return {
            "nodes": [
                {"statusCheckRollup": {"state": s} if s else None}
                for s in (states if states is not None else [])
            ]
        }

    payload: dict = {
        "nameWithOwner": full_name,
        "name": name,
        "owner": {"login": owner},
        "isPrivate": is_private,
        "primaryLanguage": {"name": primary_language} if primary_language else None,
        "defaultBranchRef": {
            "name": "main",
            "target": {
                "oid": "abc123",
                "statusCheckRollup": {"state": main_state} if main_state else None,
                "history": _history(main_history_states),
            },
        },
        "_dev": None,
        "_rootTree": {"entries": [{"name": e} for e in (root_entries or [])]},
        "pullRequests": {
            "totalCount": pr_total if pr_total is not None else len(pr_nodes or []),
            "nodes": pr_nodes or [],
        },
        "issues": {
            "totalCount": issue_total if issue_total is not None else len(issue_nodes or []),
            "nodes": issue_nodes or [],
        },
    }
    if has_dev:
        payload["_dev"] = {
            "target": {
                "oid": "def456",
                "statusCheckRollup": {"state": dev_state} if dev_state else None,
                "history": _history(dev_history_states),
            },
        }
    hits = renovate_hits or {}
    for i, path in enumerate(RENOVATE_PATHS):
        payload[f"_renovate_{i}"] = (
            {"text": hits[path], "isTruncated": False} if path in hits else None
        )
    hits = pipeline_hits or {}
    for i, path in enumerate(PIPELINE_PATHS):
        payload[f"_pipeline_{i}"] = (
            {"text": hits[path], "isTruncated": False} if path in hits else None
        )
    return payload


# ---------------------------------------------------------------------------
# build_query
# ---------------------------------------------------------------------------


class TestBuildQuery:
    def test_includes_alias_per_repo(self):
        query = build_query([_mock_repo("org/a"), _mock_repo("org/b")])
        assert "r0: repository" in query
        assert "r1: repository" in query
        assert 'owner: "org"' in query
        assert 'name: "a"' in query
        assert 'name: "b"' in query

    def test_escapes_quotes_in_owner_and_name(self):
        # Defensive -- GitHub won't allow these chars, but we still escape.
        query = build_query([_mock_repo('foo"bar/baz')])
        assert '\\"' in query

    def test_fragment_includes_renovate_and_pipeline_probes(self):
        query = build_query([_mock_repo("org/a")])
        # All canonical Renovate paths appear in the fragment.
        for path in RENOVATE_PATHS:
            assert path in query
        for path in PIPELINE_PATHS:
            assert path in query

    def test_rate_limit_included(self):
        query = build_query([_mock_repo("org/a")])
        assert "rateLimit" in query


# ---------------------------------------------------------------------------
# parse_response
# ---------------------------------------------------------------------------


class TestParseResponse:
    def test_parses_single_happy_repo(self):
        payload = _graphql_repo_payload(
            full_name="org/a",
            main_state="SUCCESS",
            primary_language="Python",
            root_entries=["workspace.yaml", "pyproject.toml"],
        )
        response = {
            "data": {
                "r0": payload,
                "rateLimit": {"limit": 5000, "cost": 1, "remaining": 4999, "resetAt": None},
            }
        }
        snapshots, errors, rate = parse_response(response, [_mock_repo("org/a")])
        assert errors == {}
        assert "org/a" in snapshots
        snap = snapshots["org/a"]
        assert isinstance(snap, RepoSnapshot)
        assert snap.primary_language == "Python"
        assert snap.main_rollup_state == "SUCCESS"
        assert "workspace.yaml" in snap.root_entries
        assert rate.get("cost") == 1

    def test_missing_repo_payload_becomes_error(self):
        response = {"data": {"r0": None}}
        snapshots, errors, _ = parse_response(response, [_mock_repo("org/missing")])
        assert "org/missing" in errors
        assert "org/missing" not in snapshots

    def test_top_level_error_maps_to_repo(self):
        response = {
            "data": {"r0": None},
            "errors": [{"path": ["r0"], "message": "Not Found"}],
        }
        snapshots, errors, _ = parse_response(response, [_mock_repo("org/gone")])
        assert errors.get("org/gone") == "Not Found"

    def test_dev_branch_detected(self):
        payload = _graphql_repo_payload(
            full_name="org/service",
            has_dev=True,
            main_state="SUCCESS",
            dev_state="FAILURE",
        )
        response = {"data": {"r0": payload}}
        snapshots, _, _ = parse_response(response, [_mock_repo("org/service")])
        snap = snapshots["org/service"]
        assert snap.has_dev_branch
        assert snap.dev_rollup_state == "FAILURE"

    def test_pr_fields_parsed(self):
        payload = _graphql_repo_payload(
            full_name="org/a",
            pr_nodes=[
                {
                    "number": 42,
                    "isDraft": True,
                    "createdAt": "2026-04-20T10:00:00Z",
                    "url": "https://github.com/org/a/pull/42",
                    "author": {"login": "renovate[bot]"},
                }
            ],
            pr_total=7,
        )
        response = {"data": {"r0": payload}}
        snapshots, _, _ = parse_response(response, [_mock_repo("org/a")])
        snap = snapshots["org/a"]
        assert snap.pr_total_count == 7
        assert len(snap.pull_requests) == 1
        pr = snap.pull_requests[0]
        assert pr.number == 42
        assert pr.is_draft is True
        assert pr.author_login == "renovate[bot]"
        assert pr.url.endswith("/pull/42")

    def test_renovate_and_pipeline_text_surfaces(self):
        payload = _graphql_repo_payload(
            full_name="org/a",
            renovate_hits={"renovate.json5": '{"extends":["config:base"]}'},
            pipeline_hits={".github/workflows/pipeline.yaml": "jobs:\n  unit-tests: {}"},
        )
        response = {"data": {"r0": payload}}
        snapshots, _, _ = parse_response(response, [_mock_repo("org/a")])
        snap = snapshots["org/a"]
        rpath, rtext = pick_renovate_config(snap)
        assert rpath == "renovate.json5"
        assert "config:base" in rtext
        ppath, ptext = pick_pipeline_yaml(snap)
        assert ppath == ".github/workflows/pipeline.yaml"
        assert "unit-tests" in ptext

    def test_rollup_walks_history_when_tip_has_no_rollup(self):
        # Skip-ci release commit at tip -> null rollup on tip. History walkback
        # should find the prior merge commit's SUCCESS state.
        payload = _graphql_repo_payload(
            full_name="org/a",
            main_state="",  # empty string -> no statusCheckRollup on tip
            main_history_states=[None, "SUCCESS", "SUCCESS"],
        )
        # Force the tip rollup to null; main_state="" above already does this
        # but be explicit.
        payload["defaultBranchRef"]["target"]["statusCheckRollup"] = None
        response = {"data": {"r0": payload}}
        snapshots, _, _ = parse_response(response, [_mock_repo("org/a")])
        assert snapshots["org/a"].main_rollup_state == "SUCCESS"

    def test_rollup_stays_unknown_when_history_also_empty(self):
        payload = _graphql_repo_payload(
            full_name="org/a",
            main_state="",
            main_history_states=[None, None, None],
        )
        payload["defaultBranchRef"]["target"]["statusCheckRollup"] = None
        response = {"data": {"r0": payload}}
        snapshots, _, _ = parse_response(response, [_mock_repo("org/a")])
        assert snapshots["org/a"].main_rollup_state is None

    def test_rollup_prefers_tip_when_present(self):
        # Tip has a rollup -- history shouldn't be consulted, so put a bogus
        # state there and confirm we still get the tip's.
        payload = _graphql_repo_payload(
            full_name="org/a",
            main_state="FAILURE",
            main_history_states=["SUCCESS", "SUCCESS"],
        )
        response = {"data": {"r0": payload}}
        snapshots, _, _ = parse_response(response, [_mock_repo("org/a")])
        assert snapshots["org/a"].main_rollup_state == "FAILURE"

    def test_dev_rollup_walks_history_too(self):
        payload = _graphql_repo_payload(
            full_name="org/service",
            has_dev=True,
            main_state="SUCCESS",
            dev_state="",
            dev_history_states=[None, "FAILURE"],
        )
        payload["_dev"]["target"]["statusCheckRollup"] = None
        response = {"data": {"r0": payload}}
        snapshots, _, _ = parse_response(response, [_mock_repo("org/service")])
        assert snapshots["org/service"].dev_rollup_state == "FAILURE"

    def test_truncated_blob_treated_as_absent(self):
        payload = _graphql_repo_payload(full_name="org/a")
        # Manually mark the renovate.json5 blob as truncated.
        payload["_renovate_0"] = {"text": "partial", "isTruncated": True}
        response = {"data": {"r0": payload}}
        snapshots, _, _ = parse_response(response, [_mock_repo("org/a")])
        snap = snapshots["org/a"]
        rpath, _ = pick_renovate_config(snap)
        assert rpath is None


# ---------------------------------------------------------------------------
# translate_rollup_state
# ---------------------------------------------------------------------------


class TestTranslateRollupState:
    def test_success(self):
        assert translate_rollup_state("SUCCESS") == "success"

    def test_failure_and_error(self):
        assert translate_rollup_state("FAILURE") == "failure"
        assert translate_rollup_state("ERROR") == "failure"

    def test_pending(self):
        assert translate_rollup_state("PENDING") == "in_progress"
        assert translate_rollup_state("EXPECTED") == "in_progress"

    def test_none_is_unknown(self):
        assert translate_rollup_state(None) == "unknown"
        assert translate_rollup_state("NEVERHEARDOFTHIS") == "unknown"


# ---------------------------------------------------------------------------
# fetch_workspace_snapshot (integration with mocked requester)
# ---------------------------------------------------------------------------


class TestFetchWorkspaceSnapshot:
    def test_empty_repo_list_returns_empty_snapshot(self):
        gh = MagicMock()
        result = fetch_workspace_snapshot(gh, [])
        assert result.by_full_name == {}
        assert result.errored == {}

    def test_calls_requester_once_per_batch(self):
        # Build 60 repos so the batcher has to chunk (25/25/10).
        repos = [_mock_repo(f"org/r{i}") for i in range(60)]

        call_count = 0
        chunks_seen: list[int] = []

        def _request(method, url, **kwargs):  # noqa: ARG001
            nonlocal call_count
            call_count += 1
            # Parse the query string to recover which repos are in this batch.
            # Tests with MagicMock request bodies aren't feasible, so we key
            # off alias count by reading from the query sent to us.
            query = kwargs.get("input", {}).get("query", "")
            # Count `rN: repository` aliases to know chunk size.
            chunk_size = query.count(": repository(owner:")
            chunks_seen.append(chunk_size)
            # Rebuild payloads keyed by r0..r{chunk_size-1}; parse_response
            # will map each alias back to the corresponding input repo.
            base_idx = 0 if call_count == 1 else (25 if call_count == 2 else 50)
            data = {}
            for i in range(chunk_size):
                data[f"r{i}"] = _graphql_repo_payload(
                    full_name=f"org/r{base_idx + i}",
                    main_state="SUCCESS",
                )
            data["rateLimit"] = {"limit": 5000, "cost": 1, "remaining": 4999, "resetAt": None}
            return ({}, {"data": data})

        gh = MagicMock()
        gh._Github__requester.requestJsonAndCheck.side_effect = _request  # type: ignore[attr-defined]

        result = fetch_workspace_snapshot(gh, repos)
        # 60 repos -> 3 chunks of 25/25/10.
        assert call_count == 3
        assert chunks_seen == [25, 25, 10]
        assert len(result.by_full_name) == 60
        assert result.rate_limit_cost == 3

    def test_failed_request_errors_chunk(self):
        repos = [_mock_repo(f"org/r{i}") for i in range(5)]
        gh = MagicMock()
        gh._Github__requester.requestJsonAndCheck.side_effect = RuntimeError("boom")  # type: ignore[attr-defined]
        result = fetch_workspace_snapshot(gh, repos)
        assert result.by_full_name == {}
        assert all(name in result.errored for name in (r.full_name for r in repos))

    def test_transient_network_error_is_retried(self, monkeypatch):
        from requests.exceptions import ChunkedEncodingError

        import augint_tools.dashboard._gql as gql

        monkeypatch.setattr(gql, "_RETRY_BACKOFF_SECONDS", (0.0, 0.0))

        repos = [_mock_repo("org/a")]
        calls = {"n": 0}

        def _request(method, url, **kwargs):  # noqa: ARG001
            calls["n"] += 1
            if calls["n"] < 3:
                raise ChunkedEncodingError("Connection broken: IncompleteRead")
            return (
                {},
                {
                    "data": {
                        "r0": _graphql_repo_payload(full_name="org/a", main_state="SUCCESS"),
                        "rateLimit": {
                            "limit": 5000,
                            "cost": 1,
                            "remaining": 4999,
                            "resetAt": None,
                        },
                    }
                },
            )

        gh = MagicMock()
        gh._Github__requester.requestJsonAndCheck.side_effect = _request  # type: ignore[attr-defined]

        result = fetch_workspace_snapshot(gh, repos)
        assert calls["n"] == 3
        assert "org/a" in result.by_full_name
        assert result.errored == {}

    def test_transient_network_error_gives_up_after_retries(self, monkeypatch):
        from requests.exceptions import ChunkedEncodingError

        import augint_tools.dashboard._gql as gql

        monkeypatch.setattr(gql, "_RETRY_BACKOFF_SECONDS", (0.0, 0.0))

        repos = [_mock_repo("org/a")]
        gh = MagicMock()
        gh._Github__requester.requestJsonAndCheck.side_effect = ChunkedEncodingError(  # type: ignore[attr-defined]
            "Connection broken"
        )
        result = fetch_workspace_snapshot(gh, repos)
        assert result.by_full_name == {}
        assert "org/a" in result.errored
        assert gh._Github__requester.requestJsonAndCheck.call_count == gql._RETRY_ATTEMPTS  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Pickers
# ---------------------------------------------------------------------------


class TestTeamsQueryAndParse:
    def test_build_teams_query_includes_owner_alias(self):
        query = build_teams_query(["svange", "some-org"])
        # repositoryOwner lets personal accounts resolve to User without
        # triggering a GraphQL error; the Organization fields are wrapped
        # in an inline fragment.
        assert "o0: repositoryOwner" in query
        assert "o1: repositoryOwner" in query
        assert "... on Organization" in query
        assert 'login: "svange"' in query
        assert 'login: "some-org"' in query
        assert "teams(first:" in query
        assert "repositories(first:" in query
        assert "permission" in query

    def test_parse_teams_sorts_by_permission(self):
        response = {
            "data": {
                "o0": {
                    "login": "org",
                    "teams": {
                        "nodes": [
                            {
                                "slug": "readers",
                                "name": "Readers",
                                "repositories": {
                                    "edges": [
                                        {
                                            "permission": "READ",
                                            "node": {"nameWithOwner": "org/x"},
                                        }
                                    ]
                                },
                            },
                            {
                                "slug": "admins",
                                "name": "Admins",
                                "repositories": {
                                    "edges": [
                                        {
                                            "permission": "ADMIN",
                                            "node": {"nameWithOwner": "org/x"},
                                        }
                                    ]
                                },
                            },
                        ]
                    },
                },
                "rateLimit": {
                    "limit": 5000,
                    "cost": 1,
                    "remaining": 4999,
                    "resetAt": None,
                },
            }
        }
        snapshot = parse_teams_response(response, ["org"])
        assignments = snapshot.by_full_name["org/x"]
        # Admin permission must sort before read permission.
        assert [a.slug for a in assignments] == ["admins", "readers"]
        assert snapshot.labels["admins"] == "Admins"

    def test_parse_teams_handles_personal_user_owner(self):
        # repositoryOwner resolves to a User -- no teams field, no error.
        response = {
            "data": {
                "o0": {"__typename": "User", "login": "svange"},
            }
        }
        snapshot = parse_teams_response(response, ["svange"])
        assert snapshot.by_full_name == {}
        assert snapshot.labels == {}
        assert snapshot.errored == {}

    def test_parse_teams_handles_null_owner_payload(self):
        # Defensive: even if the server returns null for the alias, don't crash.
        response = {"data": {"o0": None}}
        snapshot = parse_teams_response(response, ["svange"])
        assert snapshot.by_full_name == {}

    def test_parse_teams_records_owner_errors(self):
        response = {
            "data": {"o0": None},
            "errors": [{"path": ["o0"], "message": "Forbidden"}],
        }
        snapshot = parse_teams_response(response, ["locked-org"])
        assert snapshot.errored.get("locked-org") == "Forbidden"

    def test_fetch_workspace_teams_empty_owners_returns_empty(self):
        gh = MagicMock()
        snapshot = fetch_workspace_teams(gh, [])
        assert snapshot.by_full_name == {}
        assert snapshot.labels == {}

    def test_fetch_workspace_teams_network_failure_errors_all_owners(self):
        gh = MagicMock()
        gh._Github__requester.requestJsonAndCheck.side_effect = RuntimeError("boom")  # type: ignore[attr-defined]
        snapshot = fetch_workspace_teams(gh, ["org-a", "org-b"])
        assert "org-a" in snapshot.errored
        assert "org-b" in snapshot.errored


class TestPickers:
    def test_pick_renovate_returns_first_present(self):
        snap = RepoSnapshot(
            full_name="org/a",
            name="a",
            owner="org",
            default_branch="main",
            is_private=False,
            primary_language=None,
            has_dev_branch=False,
            main_rollup_state="SUCCESS",
            dev_rollup_state=None,
            main_head_sha=None,
            dev_head_sha=None,
            root_entries=(),
            renovate_configs={
                "renovate.json5": None,
                "renovate.json": '{"extends":[]}',
            },
        )
        path, text = pick_renovate_config(snap)
        assert path == "renovate.json"
        assert text

    def test_pick_pipeline_returns_none_when_absent(self):
        snap = RepoSnapshot(
            full_name="org/a",
            name="a",
            owner="org",
            default_branch="main",
            is_private=False,
            primary_language=None,
            has_dev_branch=False,
            main_rollup_state="SUCCESS",
            dev_rollup_state=None,
            main_head_sha=None,
            dev_head_sha=None,
            root_entries=(),
            pipeline_contents={},
        )
        assert pick_pipeline_yaml(snap) == (None, None)
