"""Tests for load_cache_context and the owners/repo_list extensions to save_cache."""

from __future__ import annotations

import json

import pytest

from augint_tools.dashboard._data import RepoStatus, load_cache_context, save_cache


def _make_status(full_name: str) -> RepoStatus:
    name = full_name.split("/")[-1]
    return RepoStatus(
        name=name,
        full_name=full_name,
        has_dev_branch=False,
        main_status="ok",
        main_error=None,
        dev_status=None,
        dev_error=None,
        open_issues=0,
        open_prs=0,
        draft_prs=0,
    )


@pytest.fixture(autouse=True)
def _clean_cache(tmp_path, monkeypatch):
    """Redirect CACHE_FILE and CACHE_DIR to a temp directory for each test."""
    import augint_tools.dashboard._data as _data_mod

    cache_dir = tmp_path / "cache"
    cache_file = cache_dir / "tui_cache.json"
    monkeypatch.setattr(_data_mod, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(_data_mod, "CACHE_FILE", cache_file)
    yield cache_file


class TestLoadCacheContextRoundTrip:
    def test_save_with_owners_then_load_returns_them(self, _clean_cache):
        statuses = [
            _make_status("org/repo-a"),
            _make_status("org/repo-b"),
        ]
        owners = ["org"]

        save_cache(statuses, owners=owners)
        result = load_cache_context()

        assert result is not None
        assert result["owners"] == owners
        assert result["repo_list"] == ["org/repo-a", "org/repo-b"]

    def test_repo_list_order_matches_statuses_order(self, _clean_cache):
        statuses = [
            _make_status("org/zebra"),
            _make_status("org/apple"),
            _make_status("org/mango"),
        ]
        save_cache(statuses, owners=["org"])
        result = load_cache_context()

        assert result["repo_list"] == ["org/zebra", "org/apple", "org/mango"]

    def test_multiple_owners_preserved(self, _clean_cache):
        statuses = [_make_status("org-a/repo"), _make_status("org-b/repo")]
        owners = ["org-a", "org-b"]

        save_cache(statuses, owners=owners)
        result = load_cache_context()

        assert result["owners"] == ["org-a", "org-b"]


class TestLoadCacheContextMissingFile:
    def test_returns_none_when_file_absent(self, _clean_cache):
        # _clean_cache fixture redirects to tmp_path; no file written yet
        result = load_cache_context()
        assert result is None


class TestLoadCacheContextLegacyFormat:
    def test_returns_none_when_keys_absent(self, _clean_cache, monkeypatch):
        import augint_tools.dashboard._data as _data_mod

        cache_dir = _data_mod.CACHE_DIR
        cache_file = _data_mod.CACHE_FILE
        cache_dir.mkdir(parents=True, exist_ok=True)
        # Write a cache file that has repos but no repo_list/owners keys
        legacy = {
            "repos": {
                "org/repo": {
                    "name": "repo",
                    "full_name": "org/repo",
                    "has_dev_branch": False,
                    "main_status": "ok",
                    "main_error": None,
                    "dev_status": None,
                    "dev_error": None,
                    "open_issues": 0,
                    "open_prs": 0,
                    "draft_prs": 0,
                }
            }
        }
        cache_file.write_text(json.dumps(legacy))

        result = load_cache_context()
        assert result is None


class TestSaveCacheOwnersPreservation:
    def test_owners_preserved_across_status_only_save(self, _clean_cache):
        """When save_cache is called without owners, existing owners carry forward."""
        statuses = [_make_status("org/repo-a")]
        owners = ["org"]

        # First save: write owners
        save_cache(statuses, owners=owners)

        # Second save: no owners passed -- should carry forward from file
        statuses2 = [_make_status("org/repo-a"), _make_status("org/repo-b")]
        save_cache(statuses2)

        result = load_cache_context()
        assert result is not None
        assert result["owners"] == ["org"]
        # repo_list should also be preserved from the first save
        assert result["repo_list"] == ["org/repo-a"]

    def test_owners_overwritten_when_provided(self, _clean_cache):
        """When owners is explicitly provided, it replaces the old value."""
        statuses = [_make_status("org/repo-a")]
        save_cache(statuses, owners=["org-old"])

        statuses2 = [_make_status("new-org/repo-x")]
        save_cache(statuses2, owners=["new-org"])

        result = load_cache_context()
        assert result["owners"] == ["new-org"]
        assert result["repo_list"] == ["new-org/repo-x"]
