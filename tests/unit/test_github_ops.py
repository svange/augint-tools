"""Tests for GitHub PR and issue helpers."""

import json
from subprocess import CompletedProcess
from unittest.mock import patch

from augint_tools.github.issues import Issue, list_issues
from augint_tools.github.prs import PullRequest, create_pr, enable_automerge, get_open_prs


def _ok(stdout: str = "") -> CompletedProcess:
    return CompletedProcess(args=[], returncode=0, stdout=stdout)


def _fail(stdout: str = "") -> CompletedProcess:
    return CompletedProcess(args=[], returncode=1, stdout=stdout)


class TestGetOpenPrs:
    def test_returns_parsed_prs(self):
        payload = json.dumps(
            [
                {
                    "number": 1,
                    "title": "one",
                    "state": "OPEN",
                    "url": "u1",
                    "headRefName": "feat/a",
                },
                {
                    "number": 2,
                    "title": "two",
                    "state": "OPEN",
                    "url": "u2",
                    "headRefName": "feat/b",
                },
            ]
        )
        with patch("augint_tools.github.prs.run_gh", return_value=_ok(payload)) as mock_run:
            prs = get_open_prs()
        assert [p.number for p in prs] == [1, 2]
        assert prs[0] == PullRequest(
            number=1, title="one", state="OPEN", url="u1", head_ref="feat/a"
        )
        # Default call carries no --repo / --head.
        cmd = mock_run.call_args[0][0]
        assert "--repo" not in cmd
        assert "--head" not in cmd

    def test_passes_repo_and_branch_filters(self):
        with patch("augint_tools.github.prs.run_gh", return_value=_ok("[]")) as mock_run:
            get_open_prs(branch="feat/x", repo="org/repo")
        cmd = mock_run.call_args[0][0]
        assert cmd[-4:] == ["--repo", "org/repo", "--head", "feat/x"]

    def test_nonzero_returns_empty(self):
        with patch("augint_tools.github.prs.run_gh", return_value=_fail()):
            assert get_open_prs() == []

    def test_invalid_json_returns_empty(self):
        with patch("augint_tools.github.prs.run_gh", return_value=_ok("not-json")):
            assert get_open_prs() == []

    def test_exception_returns_empty(self):
        with patch("augint_tools.github.prs.run_gh", side_effect=RuntimeError("boom")):
            assert get_open_prs() == []


class TestCreatePr:
    def test_returns_url_on_success(self):
        with patch(
            "augint_tools.github.prs.run_gh",
            return_value=_ok("https://github.com/org/repo/pull/9\n"),
        ) as mock_run:
            url = create_pr(title="t", base="main", head="feat/x", body="b", repo="org/repo")
        assert url == "https://github.com/org/repo/pull/9"
        cmd = mock_run.call_args[0][0]
        # Title/base/body always present; repo and head appended when given.
        assert cmd[:7] == ["pr", "create", "--title", "t", "--base", "main", "--body"]
        assert "--repo" in cmd and "org/repo" in cmd
        assert "--head" in cmd and "feat/x" in cmd

    def test_omits_optional_flags(self):
        with patch("augint_tools.github.prs.run_gh", return_value=_ok("url\n")) as mock_run:
            create_pr(title="t", base="main")
        cmd = mock_run.call_args[0][0]
        assert "--repo" not in cmd
        assert "--head" not in cmd

    def test_returns_none_on_failure(self):
        with patch("augint_tools.github.prs.run_gh", return_value=_fail()):
            assert create_pr(title="t", base="main") is None

    def test_returns_none_on_exception(self):
        with patch("augint_tools.github.prs.run_gh", side_effect=RuntimeError("boom")):
            assert create_pr(title="t", base="main") is None


class TestEnableAutomerge:
    def test_success_default_method(self):
        with patch("augint_tools.github.prs.run_gh", return_value=_ok()) as mock_run:
            assert enable_automerge(42) is True
        cmd = mock_run.call_args[0][0]
        assert cmd == ["pr", "merge", "42", "--auto", "--merge"]

    def test_method_and_repo(self):
        with patch("augint_tools.github.prs.run_gh", return_value=_ok()) as mock_run:
            assert enable_automerge(7, repo="org/repo", method="squash") is True
        cmd = mock_run.call_args[0][0]
        assert cmd == ["pr", "merge", "7", "--auto", "--squash", "--repo", "org/repo"]

    def test_failure(self):
        with patch("augint_tools.github.prs.run_gh", return_value=_fail()):
            assert enable_automerge(1) is False

    def test_exception(self):
        with patch("augint_tools.github.prs.run_gh", side_effect=RuntimeError("boom")):
            assert enable_automerge(1) is False


class TestListIssues:
    def _payload(self) -> str:
        return json.dumps(
            [
                {
                    "number": 1,
                    "title": "one",
                    "state": "OPEN",
                    "url": "u1",
                    "labels": [{"name": "bug"}, {"name": "p1"}],
                },
                {
                    "number": 2,
                    "title": "two",
                    "state": "CLOSED",
                    "url": "u2",
                    "labels": [],
                },
            ]
        )

    def test_returns_parsed_issues(self):
        with patch("augint_tools.github.issues.run_gh", return_value=_ok(self._payload())):
            issues = list_issues()
        assert issues[0] == Issue(
            number=1, title="one", state="OPEN", labels=["bug", "p1"], url="u1"
        )
        assert issues[1].labels == []

    def test_label_query_uses_label_flag(self):
        with patch("augint_tools.github.issues.run_gh", return_value=_ok("[]")) as mock_run:
            list_issues(query="bug")
        cmd = mock_run.call_args[0][0]
        assert "--label" in cmd and "bug" in cmd
        assert "--search" not in cmd

    def test_search_query_uses_search_flag(self):
        with patch("augint_tools.github.issues.run_gh", return_value=_ok("[]")) as mock_run:
            list_issues(query="is:open needs triage")
        cmd = mock_run.call_args[0][0]
        assert "--search" in cmd and "is:open needs triage" in cmd
        assert "--label" not in cmd

    def test_explicit_label_prefix_uses_search(self):
        # A "label:" prefixed query is treated as a raw search, not as --label.
        with patch("augint_tools.github.issues.run_gh", return_value=_ok("[]")) as mock_run:
            list_issues(query="label:bug")
        cmd = mock_run.call_args[0][0]
        assert "--search" in cmd and "label:bug" in cmd

    def test_repo_passed_through(self):
        with patch("augint_tools.github.issues.run_gh", return_value=_ok("[]")) as mock_run:
            list_issues(repo="org/repo")
        cmd = mock_run.call_args[0][0]
        assert "--repo" in cmd and "org/repo" in cmd

    def test_nonzero_returns_empty(self):
        with patch("augint_tools.github.issues.run_gh", return_value=_fail()):
            assert list_issues() == []

    def test_invalid_json_returns_empty(self):
        with patch("augint_tools.github.issues.run_gh", return_value=_ok("nope")):
            assert list_issues() == []

    def test_exception_returns_empty(self):
        with patch("augint_tools.github.issues.run_gh", side_effect=RuntimeError("boom")):
            assert list_issues() == []
