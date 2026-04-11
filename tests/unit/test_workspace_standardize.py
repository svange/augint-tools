"""Tests for workspace standardize verify aggregator."""

import json
from pathlib import Path
from unittest.mock import patch

from augint_tools.config import RepoConfig, WorkspaceConfig
from augint_tools.workspace_standardize import (
    SECTION_NAMES,
    _filter_and_order,
    _locate_sections,
    _overall_from_sections,
    _parse_verify_output,
    verify_workspace,
)


def _make_config(repos: list[RepoConfig], name: str = "test-ws") -> WorkspaceConfig:
    return WorkspaceConfig(name=name, repos_dir=".", repos=repos)


def _make_repo(
    name: str, depends_on: list[str] | None = None, path: str | None = None
) -> RepoConfig:
    return RepoConfig(
        name=name,
        path=path or name,
        url=f"https://example.invalid/{name}.git",
        repo_type="library",
        base_branch="main",
        pr_target_branch="main",
        depends_on=depends_on or [],
    )


def _clean_verify_payload() -> dict:
    """An ai-shell verify JSON payload where every section is clean."""
    return {
        "command": "standardize repo --verify",
        "scope": "repo",
        "status": "ok",
        "result": {
            "sections": {
                name: {"status": "pass", "detail": f"{name} ok"}
                for name in SECTION_NAMES
            }
        },
    }


def _drift_verify_payload() -> dict:
    """An ai-shell verify payload where pipeline and renovate drift."""
    payload = _clean_verify_payload()
    payload["result"]["sections"]["pipeline"] = {
        "status": "drift",
        "detail": "missing: Code quality",
    }
    payload["result"]["sections"]["renovate"] = {
        "status": "drift",
        "detail": "renovate.json5 differs",
    }
    return payload


class TestLocateSections:
    def test_envelope_with_result_sections(self):
        payload = {"result": {"sections": {"detect": {"status": "pass", "detail": ""}}}}
        sections = _locate_sections(payload)
        assert sections is not None
        assert "detect" in sections

    def test_envelope_with_result_as_sections(self):
        payload = {"result": {"detect": {"status": "pass", "detail": ""}}}
        sections = _locate_sections(payload)
        assert sections is not None
        assert "detect" in sections

    def test_top_level_sections(self):
        payload = {"sections": {"pipeline": {"status": "drift", "detail": "x"}}}
        sections = _locate_sections(payload)
        assert sections is not None
        assert "pipeline" in sections

    def test_unrecognized_shape_returns_none(self):
        assert _locate_sections({"foo": "bar"}) is None
        assert _locate_sections({"result": {"unrelated": 123}}) is None
        assert _locate_sections("not a dict") is None


class TestParseVerifyOutput:
    def test_clean_payload(self):
        sections, err = _parse_verify_output(json.dumps(_clean_verify_payload()))
        assert err is None
        assert len(sections) == len(SECTION_NAMES)
        assert all(s.status == "pass" for s in sections.values())

    def test_drift_payload(self):
        sections, err = _parse_verify_output(json.dumps(_drift_verify_payload()))
        assert err is None
        assert sections["pipeline"].status == "drift"
        assert sections["pipeline"].detail == "missing: Code quality"
        assert sections["detect"].status == "pass"

    def test_empty_output(self):
        sections, err = _parse_verify_output("")
        assert err == "ai-shell produced no output"
        assert sections == {}

    def test_invalid_json(self):
        sections, err = _parse_verify_output("not json")
        assert err is not None
        assert "not valid JSON" in err

    def test_missing_sections(self):
        sections, err = _parse_verify_output(json.dumps({"result": {}}))
        assert err is not None
        assert sections == {}

    def test_unknown_status_becomes_fail(self):
        """Unknown section status must not silently drop to pass."""
        payload = {"result": {"sections": {"detect": {"status": "weird", "detail": "x"}}}}
        sections, err = _parse_verify_output(json.dumps(payload))
        assert err is None
        assert sections["detect"].status == "fail"

    def test_ok_status_normalized_to_pass(self):
        payload = {"result": {"sections": {"detect": {"status": "ok", "detail": "x"}}}}
        sections, err = _parse_verify_output(json.dumps(payload))
        assert err is None
        assert sections["detect"].status == "pass"


class TestOverallFromSections:
    def test_all_pass(self):
        sections, _ = _parse_verify_output(json.dumps(_clean_verify_payload()))
        assert _overall_from_sections(sections) == "pass"

    def test_any_drift(self):
        sections, _ = _parse_verify_output(json.dumps(_drift_verify_payload()))
        assert _overall_from_sections(sections) == "drift"

    def test_fail_beats_drift(self):
        payload = _drift_verify_payload()
        payload["result"]["sections"]["release"] = {"status": "fail", "detail": "x"}
        sections, _ = _parse_verify_output(json.dumps(payload))
        assert _overall_from_sections(sections) == "fail"


class TestFilterAndOrder:
    def test_depends_on_ordering(self):
        """Declaration order [b, a] with b depends_on=[a] must flip to [a, b]."""
        repo_a = _make_repo("a")
        repo_b = _make_repo("b", depends_on=["a"])
        config = _make_config([repo_b, repo_a])
        ordered, source = _filter_and_order(config, only=None)
        assert [r.name for r in ordered] == ["a", "b"]
        assert source == "depends_on"

    def test_no_depends_on_uses_declaration(self):
        config = _make_config([_make_repo("a"), _make_repo("b")])
        ordered, source = _filter_and_order(config, only=None)
        assert [r.name for r in ordered] == ["a", "b"]
        assert source == "declaration"

    def test_only_filter_preserves_topo_order(self):
        """--only picks a subset but keeps dependency order intact."""
        repo_a = _make_repo("a")
        repo_b = _make_repo("b", depends_on=["a"])
        repo_c = _make_repo("c", depends_on=["b"])
        config = _make_config([repo_c, repo_a, repo_b])
        ordered, source = _filter_and_order(config, only=["c", "a"])
        assert [r.name for r in ordered] == ["a", "c"]
        assert source == "depends_on"


class TestVerifyWorkspace:
    def test_all_clean(self, tmp_path):
        (tmp_path / "lib-a").mkdir()
        (tmp_path / "lib-b").mkdir()
        config = _make_config(
            [_make_repo("lib-a", path="lib-a"), _make_repo("lib-b", path="lib-b")]
        )

        clean_stdout = json.dumps(_clean_verify_payload())
        with patch(
            "augint_tools.workspace_standardize._run_ai_shell_verify",
            return_value=(0, clean_stdout, ""),
        ):
            result = verify_workspace(tmp_path, config)

        assert result.status == "ok"
        assert result.exit_code == 0
        assert result.aggregate["repos_clean"] == 2
        assert result.aggregate["repos_drift"] == 0
        assert result.aggregate["repos_error"] == 0
        assert result.aggregate["total_sections_drift"] == 0
        assert all(r.overall == "pass" for r in result.repos)

    def test_drift_across_children(self, tmp_path):
        (tmp_path / "lib-a").mkdir()
        (tmp_path / "lib-b").mkdir()
        config = _make_config(
            [_make_repo("lib-a", path="lib-a"), _make_repo("lib-b", path="lib-b")]
        )

        drift_stdout = json.dumps(_drift_verify_payload())
        with patch(
            "augint_tools.workspace_standardize._run_ai_shell_verify",
            return_value=(0, drift_stdout, ""),
        ):
            result = verify_workspace(tmp_path, config)

        assert result.status == "drift"
        assert result.exit_code == 1
        assert result.aggregate["repos_drift"] == 2
        # Two drift sections per repo, two repos -> 4.
        assert result.aggregate["total_sections_drift"] == 4

    def test_missing_child_path_becomes_error(self, tmp_path):
        """A child whose path doesn't exist must become overall=error and flip workspace to error."""
        (tmp_path / "lib-a").mkdir()
        config = _make_config(
            [
                _make_repo("lib-a", path="lib-a"),
                _make_repo("lib-b", path="lib-b"),  # not created
            ]
        )

        clean_stdout = json.dumps(_clean_verify_payload())
        with patch(
            "augint_tools.workspace_standardize._run_ai_shell_verify",
            return_value=(0, clean_stdout, ""),
        ) as mock_run:
            result = verify_workspace(tmp_path, config)

        # Only lib-a should have had ai-shell invoked.
        assert mock_run.call_count == 1
        assert result.status == "error"
        assert result.exit_code == 2
        missing = next(r for r in result.repos if r.name == "lib-b")
        assert missing.present is False
        assert missing.overall == "error"
        assert missing.error == "repository path does not exist"
        assert any("lib-b" in e for e in result.errors)

    def test_ai_shell_launch_failure(self, tmp_path):
        (tmp_path / "lib-a").mkdir()
        config = _make_config([_make_repo("lib-a", path="lib-a")])

        with patch(
            "augint_tools.workspace_standardize._run_ai_shell_verify",
            return_value=(-1, "", "ai-shell executable not found on PATH"),
        ):
            result = verify_workspace(tmp_path, config)

        assert result.status == "error"
        assert result.exit_code == 2
        assert result.repos[0].overall == "error"
        assert "not found" in result.repos[0].error

    def test_only_filter(self, tmp_path):
        (tmp_path / "lib-a").mkdir()
        (tmp_path / "lib-b").mkdir()
        (tmp_path / "lib-c").mkdir()
        config = _make_config(
            [
                _make_repo("lib-a", path="lib-a"),
                _make_repo("lib-b", path="lib-b"),
                _make_repo("lib-c", path="lib-c"),
            ]
        )

        clean_stdout = json.dumps(_clean_verify_payload())
        with patch(
            "augint_tools.workspace_standardize._run_ai_shell_verify",
            return_value=(0, clean_stdout, ""),
        ) as mock_run:
            result = verify_workspace(tmp_path, config, only=["lib-a", "lib-c"])

        assert mock_run.call_count == 2
        names = [r.name for r in result.repos]
        assert names == ["lib-a", "lib-c"]
        assert result.aggregate["repos_checked"] == 2

    def test_no_cd_into_child(self, tmp_path):
        """Ticket acceptance: subprocess must be invoked with the child path as an argument,
        not by cding into the child. We assert the child path was passed absolute."""
        (tmp_path / "lib-a").mkdir()
        config = _make_config([_make_repo("lib-a", path="lib-a")])

        recorded: dict = {}

        def fake_run(repo_path: Path):
            recorded["path"] = repo_path
            return 0, json.dumps(_clean_verify_payload()), ""

        with patch(
            "augint_tools.workspace_standardize._run_ai_shell_verify",
            side_effect=fake_run,
        ):
            verify_workspace(tmp_path, config)

        assert recorded["path"].is_absolute()
        assert recorded["path"] == tmp_path / "lib-a"
