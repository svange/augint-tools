"""Tests for workspace standardize verify aggregator."""

from pathlib import Path
from unittest.mock import patch

from augint_tools.config import RepoConfig, WorkspaceConfig
from augint_tools.workspace_standardize import (
    _filter_and_order,
    _overall_from_sections,
    _parse_verify_text,
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


def _clean_verify_text() -> str:
    """All 9 canonical sections clean."""
    return (
        "[PASS] detect: python/library\n"
        "[PASS] pipeline: all jobs present\n"
        "[PASS] precommit: .pre-commit-config.yaml matches template\n"
        "[PASS] renovate: renovate.json5 matches template\n"
        "[PASS] release: [tool.semantic_release] matches canon\n"
        "[PASS] dotfiles: .editorconfig, .gitignore match canon\n"
        "[PASS] repo_settings: all settings match\n"
        "[PASS] rulesets: library\n"
        "[PASS] oidc: oidc role matches canon\n"
    )


def _drift_verify_text() -> str:
    """Two drift sections out of nine — the classic landline-scrubber case."""
    return (
        "[PASS] detect: python/library\n"
        "[DRIFT] pipeline: missing: Code quality, Security, Compliance, Build validation\n"
        "[PASS] precommit: .pre-commit-config.yaml matches template\n"
        "[DRIFT] renovate: /tmp/foo/renovate.json5 differs from template\n"
        "[PASS] release: [tool.semantic_release] matches canon\n"
        "[PASS] dotfiles: .editorconfig, .gitignore match canon\n"
        "[PASS] repo_settings: all settings match\n"
        "[PASS] rulesets: library\n"
        "[PASS] oidc: oidc role matches canon\n"
    )


class TestParseVerifyText:
    def test_all_clean(self):
        sections, warnings, err = _parse_verify_text(_clean_verify_text())
        assert err is None
        assert warnings == []
        assert len(sections) == 9
        assert all(s.status == "pass" for s in sections.values())
        assert sections["detect"].detail == "python/library"

    def test_drift_output(self):
        sections, warnings, err = _parse_verify_text(_drift_verify_text())
        assert err is None
        assert warnings == []
        assert sections["pipeline"].status == "drift"
        assert (
            sections["pipeline"].detail
            == "missing: Code quality, Security, Compliance, Build validation"
        )
        assert sections["renovate"].status == "drift"
        assert sections["detect"].status == "pass"

    def test_fail_status(self):
        text = "[FAIL] release: semantic-release config is malformed\n"
        sections, warnings, err = _parse_verify_text(text)
        assert err is None
        assert warnings == []
        assert sections["release"].status == "fail"

    def test_empty_output_is_error(self):
        sections, warnings, err = _parse_verify_text("")
        assert err == "ai-shell produced no output"
        assert sections == {}
        assert warnings == []

    def test_whitespace_only_is_error(self):
        sections, warnings, err = _parse_verify_text("   \n\t\n")
        assert err == "ai-shell produced no output"
        assert sections == {}

    def test_no_recognizable_sections(self):
        text = "some stray stderr\nanother stray line\n"
        sections, warnings, err = _parse_verify_text(text)
        assert err == "no recognizable sections in ai-shell output"
        assert sections == {}
        # Every stray line should have produced a warning.
        assert len(warnings) == 2
        assert all("unparseable line" in w for w in warnings)

    def test_continuation_line_appended_to_previous_detail(self):
        """Lines starting with whitespace extend the most recent section."""
        text = (
            "[DRIFT] pipeline: missing: Code quality, Security,\n"
            "    Compliance, Build validation\n"
            "[PASS] detect: python/library\n"
        )
        sections, warnings, err = _parse_verify_text(text)
        assert err is None
        assert warnings == []
        assert sections["pipeline"].status == "drift"
        assert (
            sections["pipeline"].detail
            == "missing: Code quality, Security, Compliance, Build validation"
        )
        assert sections["detect"].status == "pass"

    def test_duplicate_section_warns_and_keeps_last(self):
        text = "[PASS] pipeline: all jobs present\n[DRIFT] pipeline: missing: Build validation\n"
        sections, warnings, err = _parse_verify_text(text)
        assert err is None
        assert sections["pipeline"].status == "drift"
        assert sections["pipeline"].detail == "missing: Build validation"
        assert len(warnings) == 1
        assert "multiple times" in warnings[0]
        assert "pipeline" in warnings[0]

    def test_unparseable_line_warns_but_keeps_parsing(self):
        """A stray non-matching line is logged as a warning; parsing continues."""
        text = (
            "[PASS] detect: python/library\n"
            "Warning: some stray message from ai-shell\n"
            "[DRIFT] pipeline: missing: Code quality\n"
        )
        sections, warnings, err = _parse_verify_text(text)
        assert err is None
        assert len(sections) == 2
        assert sections["detect"].status == "pass"
        assert sections["pipeline"].status == "drift"
        assert len(warnings) == 1
        assert "stray message" in warnings[0]

    def test_long_unparseable_line_truncated_in_warning(self):
        text = "[PASS] detect: python/library\n" + ("x" * 500) + "\n"
        _, warnings, err = _parse_verify_text(text)
        assert err is None
        assert len(warnings) == 1
        # 120-char cap (+ ellipsis) — must not dump the whole 500-char line.
        assert len(warnings[0]) < 200


class TestOverallFromSections:
    def test_all_pass(self):
        sections, _, _ = _parse_verify_text(_clean_verify_text())
        assert _overall_from_sections(sections) == "pass"

    def test_any_drift(self):
        sections, _, _ = _parse_verify_text(_drift_verify_text())
        assert _overall_from_sections(sections) == "drift"

    def test_fail_beats_drift(self):
        text = (
            "[DRIFT] pipeline: missing: Code quality\n"
            "[FAIL] release: semantic-release config is malformed\n"
        )
        sections, _, _ = _parse_verify_text(text)
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

        with patch(
            "augint_tools.workspace_standardize._run_ai_shell_verify",
            return_value=(0, _clean_verify_text(), ""),
        ):
            result = verify_workspace(tmp_path, config)

        assert result.status == "ok"
        assert result.exit_code == 0
        assert result.aggregate["repos_clean"] == 2
        assert result.aggregate["repos_drift"] == 0
        assert result.aggregate["repos_error"] == 0
        assert result.aggregate["total_sections_drift"] == 0
        assert all(r.overall == "pass" for r in result.repos)
        assert result.warnings == []
        assert result.errors == []

    def test_drift_across_children(self, tmp_path):
        (tmp_path / "lib-a").mkdir()
        (tmp_path / "lib-b").mkdir()
        config = _make_config(
            [_make_repo("lib-a", path="lib-a"), _make_repo("lib-b", path="lib-b")]
        )

        with patch(
            "augint_tools.workspace_standardize._run_ai_shell_verify",
            return_value=(0, _drift_verify_text(), ""),
        ):
            result = verify_workspace(tmp_path, config)

        assert result.status == "drift"
        assert result.exit_code == 1
        assert result.aggregate["repos_drift"] == 2
        # Two drift sections (pipeline, renovate) per repo, two repos -> 4.
        assert result.aggregate["total_sections_drift"] == 4
        assert result.errors == []
        assert result.warnings == []

    def test_missing_child_path_becomes_error(self, tmp_path):
        """A child whose path doesn't exist must become overall=error and flip
        the workspace to error."""
        (tmp_path / "lib-a").mkdir()
        config = _make_config(
            [
                _make_repo("lib-a", path="lib-a"),
                _make_repo("lib-b", path="lib-b"),  # not created
            ]
        )

        with patch(
            "augint_tools.workspace_standardize._run_ai_shell_verify",
            return_value=(0, _clean_verify_text(), ""),
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
        assert result.repos[0].error is not None
        assert "not found" in result.repos[0].error

    def test_ai_shell_nonzero_exit_is_error(self, tmp_path):
        """Ticket T6-3: ai-shell exits 0 on drift; non-zero means it broke.

        Non-zero exit must flip the child to overall=error even if stdout
        happens to contain parseable-looking lines.
        """
        (tmp_path / "lib-a").mkdir()
        config = _make_config([_make_repo("lib-a", path="lib-a")])

        with patch(
            "augint_tools.workspace_standardize._run_ai_shell_verify",
            return_value=(2, "", "Error: path '/bogus' does not exist"),
        ):
            result = verify_workspace(tmp_path, config)

        assert result.status == "error"
        assert result.repos[0].overall == "error"
        assert result.repos[0].error is not None
        assert "does not exist" in result.repos[0].error

    def test_empty_stdout_is_error(self, tmp_path):
        (tmp_path / "lib-a").mkdir()
        config = _make_config([_make_repo("lib-a", path="lib-a")])

        with patch(
            "augint_tools.workspace_standardize._run_ai_shell_verify",
            return_value=(0, "", ""),
        ):
            result = verify_workspace(tmp_path, config)

        assert result.status == "error"
        assert result.repos[0].overall == "error"
        assert result.repos[0].error == "ai-shell produced no output"

    def test_parse_warnings_bubble_up_to_workspace(self, tmp_path):
        """A stray non-matching line in one child should produce a workspace
        warning prefixed with the repo name — not an error."""
        (tmp_path / "lib-a").mkdir()
        config = _make_config([_make_repo("lib-a", path="lib-a")])

        stdout = (
            "[PASS] detect: python/library\n"
            "Warning: stderr noise bleeding into stdout\n"
            "[DRIFT] pipeline: missing: Code quality\n"
        )
        with patch(
            "augint_tools.workspace_standardize._run_ai_shell_verify",
            return_value=(0, stdout, ""),
        ):
            result = verify_workspace(tmp_path, config)

        assert result.status == "drift"
        assert result.exit_code == 1
        assert len(result.warnings) == 1
        assert result.warnings[0].startswith("lib-a: ")
        assert "stderr noise" in result.warnings[0]
        # Warning must not corrupt the parsed sections.
        assert result.repos[0].sections["detect"].status == "pass"
        assert result.repos[0].sections["pipeline"].status == "drift"

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

        with patch(
            "augint_tools.workspace_standardize._run_ai_shell_verify",
            return_value=(0, _clean_verify_text(), ""),
        ) as mock_run:
            result = verify_workspace(tmp_path, config, only=["lib-a", "lib-c"])

        assert mock_run.call_count == 2
        names = [r.name for r in result.repos]
        assert names == ["lib-a", "lib-c"]
        assert result.aggregate["repos_checked"] == 2

    def test_no_cd_into_child(self, tmp_path):
        """Ticket acceptance: subprocess must be invoked with the child path as an
        argument, not by cding into the child."""
        (tmp_path / "lib-a").mkdir()
        config = _make_config([_make_repo("lib-a", path="lib-a")])

        recorded: dict = {}

        def fake_run(repo_path: Path):
            recorded["path"] = repo_path
            return 0, _clean_verify_text(), ""

        with patch(
            "augint_tools.workspace_standardize._run_ai_shell_verify",
            side_effect=fake_run,
        ):
            verify_workspace(tmp_path, config)

        assert recorded["path"].is_absolute()
        assert recorded["path"] == tmp_path / "lib-a"


class TestRunAiShellVerify:
    """The subprocess wrapper itself: verify we drop --json (T6-3 core fix)."""

    def test_command_has_no_json_flag(self, tmp_path):
        import augint_tools.workspace_standardize as ws

        captured = {}

        class FakeProc:
            returncode = 0
            stdout = _clean_verify_text()
            stderr = ""

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return FakeProc()

        with patch("augint_tools.workspace_standardize.subprocess.run", side_effect=fake_run):
            ws._run_ai_shell_verify(tmp_path)

        assert captured["cmd"][0] == "ai-shell"
        assert "--json" not in captured["cmd"]
        assert "standardize" in captured["cmd"]
        assert "--verify" in captured["cmd"]
        assert str(tmp_path) in captured["cmd"]
