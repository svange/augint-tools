"""Tests for supplemental standardize checks (T10-3, T10-4, T12-1..T12-5)."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from augint_tools.cli.__main__ import cli
from augint_tools.standardize.checks import (
    check_ci_skip_keywords,
    check_delete_branch_on_merge,
    check_forbid_env_commit_exclusion,
    check_no_commit_to_branch_skip,
    check_pip_licenses_dep,
    check_quality_gate_thresholds,
    check_renovate_default_branch,
    check_renovate_dual_config,
    check_workflow_token_usage,
    filter_renovate_formatting_noise,
    run_supplemental_checks,
)

# =========================================================================== #
# T12-1: Dual Renovate config                                                #
# =========================================================================== #


class TestRenovateDualConfig:
    def test_no_config_files(self, tmp_path: Path) -> None:
        assert check_renovate_dual_config(tmp_path) == []

    def test_single_canonical_file(self, tmp_path: Path) -> None:
        (tmp_path / "renovate.json5").write_text("{}")
        assert check_renovate_dual_config(tmp_path) == []

    def test_single_non_canonical_file(self, tmp_path: Path) -> None:
        (tmp_path / "renovate.json").write_text("{}")
        assert check_renovate_dual_config(tmp_path) == []

    def test_dual_config_detected(self, tmp_path: Path) -> None:
        (tmp_path / "renovate.json").write_text("{}")
        (tmp_path / "renovate.json5").write_text("{}")
        findings = check_renovate_dual_config(tmp_path)
        assert len(findings) == 1
        assert findings[0]["status"] == "DRIFT"
        assert "renovate.json" in findings[0]["message"]
        assert "renovate.json5" in findings[0]["message"]
        assert "shadows" in findings[0]["message"]

    def test_package_json_renovate_key(self, tmp_path: Path) -> None:
        (tmp_path / "renovate.json5").write_text("{}")
        (tmp_path / "package.json").write_text(json.dumps({"renovate": {}}))
        findings = check_renovate_dual_config(tmp_path)
        assert len(findings) == 1
        assert "package.json[renovate]" in findings[0]["message"]

    def test_package_json_without_renovate_key(self, tmp_path: Path) -> None:
        (tmp_path / "renovate.json5").write_text("{}")
        (tmp_path / "package.json").write_text(json.dumps({"name": "test"}))
        assert check_renovate_dual_config(tmp_path) == []

    def test_triple_config_all_listed(self, tmp_path: Path) -> None:
        (tmp_path / "renovate.json").write_text("{}")
        (tmp_path / "renovate.json5").write_text("{}")
        (tmp_path / ".renovaterc").write_text("{}")
        findings = check_renovate_dual_config(tmp_path)
        assert len(findings) == 1
        msg = findings[0]["message"]
        assert "renovate.json" in msg
        assert "renovate.json5" in msg
        assert ".renovaterc" in msg


# =========================================================================== #
# T12-3: Renovate default branch check                                       #
# =========================================================================== #


class TestRenovateDefaultBranch:
    def test_no_config_file(self, tmp_path: Path) -> None:
        assert check_renovate_default_branch(tmp_path) == []

    def test_on_default_branch_skips(self, tmp_path: Path) -> None:
        (tmp_path / "renovate.json5").write_text("{}")
        with (
            patch(
                "augint_tools.standardize.checks._detect_default_branch",
                return_value="main",
            ),
            patch(
                "augint_tools.standardize.checks._current_branch",
                return_value="main",
            ),
        ):
            assert check_renovate_default_branch(tmp_path) == []

    def test_config_missing_on_default(self, tmp_path: Path) -> None:
        (tmp_path / "renovate.json5").write_text("{}")
        with (
            patch(
                "augint_tools.standardize.checks._detect_default_branch",
                return_value="main",
            ),
            patch(
                "augint_tools.standardize.checks._current_branch",
                return_value="dev",
            ),
            patch(
                "augint_tools.standardize.checks._git_show",
                return_value=None,
            ),
        ):
            findings = check_renovate_default_branch(tmp_path)
            assert len(findings) == 1
            assert findings[0]["status"] == "DRIFT"
            assert "not on main" in findings[0]["message"]

    def test_config_differs(self, tmp_path: Path) -> None:
        (tmp_path / "renovate.json5").write_text('{"schedule": ["weekly"]}')
        with (
            patch(
                "augint_tools.standardize.checks._detect_default_branch",
                return_value="main",
            ),
            patch(
                "augint_tools.standardize.checks._current_branch",
                return_value="dev",
            ),
            patch(
                "augint_tools.standardize.checks._git_show",
                return_value='{"schedule": ["monthly"]}',
            ),
        ):
            findings = check_renovate_default_branch(tmp_path)
            assert len(findings) == 1
            assert findings[0]["status"] == "DRIFT"
            assert "differs" in findings[0]["message"]

    def test_config_matches(self, tmp_path: Path) -> None:
        content = '{"schedule": ["weekly"]}'
        (tmp_path / "renovate.json5").write_text(content)
        with (
            patch(
                "augint_tools.standardize.checks._detect_default_branch",
                return_value="main",
            ),
            patch(
                "augint_tools.standardize.checks._current_branch",
                return_value="dev",
            ),
            patch(
                "augint_tools.standardize.checks._git_show",
                return_value=content,
            ),
        ):
            assert check_renovate_default_branch(tmp_path) == []

    def test_no_default_branch_detected(self, tmp_path: Path) -> None:
        (tmp_path / "renovate.json5").write_text("{}")
        with patch(
            "augint_tools.standardize.checks._detect_default_branch",
            return_value=None,
        ):
            assert check_renovate_default_branch(tmp_path) == []


# =========================================================================== #
# T12-4: CI skip keywords                                                     #
# =========================================================================== #


class TestCiSkipKeywords:
    def test_no_workflows_dir(self, tmp_path: Path) -> None:
        assert check_ci_skip_keywords(tmp_path) == []

    def test_clean_workflow(self, tmp_path: Path) -> None:
        wf = tmp_path / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "ci.yml").write_text(
            "name: CI\non: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n"
        )
        assert check_ci_skip_keywords(tmp_path) == []

    def test_skip_ci_detected(self, tmp_path: Path) -> None:
        wf = tmp_path / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "promote.yml").write_text(
            textwrap.dedent("""\
            name: Promote
            on: workflow_dispatch
            jobs:
              promote:
                runs-on: ubuntu-latest
                steps:
                  - run: |
                      gh pr create --body "[skip ci] automated"
            """)
        )
        findings = check_ci_skip_keywords(tmp_path)
        assert len(findings) == 1
        assert findings[0]["status"] == "FAIL"
        assert "[skip ci]" in findings[0]["message"]

    def test_comment_lines_ignored(self, tmp_path: Path) -> None:
        wf = tmp_path / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "ci.yml").write_text("# [skip ci] is bad\nname: CI\n")
        assert check_ci_skip_keywords(tmp_path) == []

    def test_if_conditions_ignored(self, tmp_path: Path) -> None:
        wf = tmp_path / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "ci.yml").write_text(
            "name: CI\njobs:\n  build:\n    if: \"!contains(github.event.head_commit.message, '[skip ci]')\"\n"
        )
        assert check_ci_skip_keywords(tmp_path) == []

    def test_multiple_keywords(self, tmp_path: Path) -> None:
        wf = tmp_path / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "deploy.yml").write_text('step1: "[skip ci]"\nstep2: "[no ci]"\n')
        findings = check_ci_skip_keywords(tmp_path)
        assert len(findings) == 2

    def test_case_insensitive(self, tmp_path: Path) -> None:
        wf = tmp_path / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "ci.yml").write_text('run: echo "[SKIP CI]"\n')
        findings = check_ci_skip_keywords(tmp_path)
        assert len(findings) == 1


# =========================================================================== #
# T12-5: Workflow token usage                                                 #
# =========================================================================== #


class TestWorkflowTokenUsage:
    def test_no_workflows_dir(self, tmp_path: Path) -> None:
        assert check_workflow_token_usage(tmp_path) == []

    def test_non_promote_workflow_skipped(self, tmp_path: Path) -> None:
        wf = tmp_path / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "ci.yml").write_text("check-runs: secrets.GH_TOKEN\n")
        assert check_workflow_token_usage(tmp_path) == []

    def test_promote_without_check_api(self, tmp_path: Path) -> None:
        wf = tmp_path / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "promote.yml").write_text("name: Promote\nsteps:\n  - run: echo done\n")
        assert check_workflow_token_usage(tmp_path) == []

    def test_promote_with_correct_token(self, tmp_path: Path) -> None:
        wf = tmp_path / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "promote.yml").write_text(
            textwrap.dedent("""\
            name: Promote
            permissions:
              checks: read
            jobs:
              preflight:
                steps:
                  - name: Check runs
                    run: gh api repos/owner/repo/check-runs
                    env:
                      GH_TOKEN: ${{ github.token }}
            """)
        )
        assert check_workflow_token_usage(tmp_path) == []

    def test_promote_with_pat_on_check_runs_line(self, tmp_path: Path) -> None:
        wf = tmp_path / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "promote.yml").write_text(
            textwrap.dedent("""\
            name: Promote
            permissions:
              checks: read
            jobs:
              preflight:
                steps:
                  - run: |
                      curl -H "Authorization: token ${{ secrets.GH_TOKEN }}" check-runs
            """)
        )
        findings = check_workflow_token_usage(tmp_path)
        assert any("secrets.GH_TOKEN" in f["message"] for f in findings)

    def test_promote_missing_checks_permission(self, tmp_path: Path) -> None:
        wf = tmp_path / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "promote.yml").write_text(
            textwrap.dedent("""\
            name: Promote
            permissions:
              contents: read
            jobs:
              preflight:
                steps:
                  - name: Check runs
                    run: gh api repos/owner/repo/check-runs
                    env:
                      GH_TOKEN: ${{ github.token }}
            """)
        )
        findings = check_workflow_token_usage(tmp_path)
        assert any("checks: read" in f["message"] for f in findings)

    def test_step_block_detection(self, tmp_path: Path) -> None:
        wf = tmp_path / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "promote.yml").write_text(
            textwrap.dedent("""\
            name: Promote
            permissions:
              checks: read
            jobs:
              preflight:
                steps:
                  - name: Bad step
                    env:
                      GH_TOKEN: ${{ secrets.GH_TOKEN }}
                    run: |
                      gh api repos/owner/repo/check-runs
            """)
        )
        findings = check_workflow_token_usage(tmp_path)
        assert any("step uses secrets.GH_TOKEN" in f["message"] for f in findings)


# =========================================================================== #
# T10-3: pip-licenses dependency                                              #
# =========================================================================== #


class TestPipLicensesDep:
    def test_no_workflows(self, tmp_path: Path) -> None:
        assert check_pip_licenses_dep(tmp_path) == []

    def test_no_compliance_gate(self, tmp_path: Path) -> None:
        wf = tmp_path / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "ci.yml").write_text("name: CI\nsteps:\n  - run: pytest\n")
        assert check_pip_licenses_dep(tmp_path) == []

    def test_pip_licenses_in_workflow_and_deps(self, tmp_path: Path) -> None:
        wf = tmp_path / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "ci.yml").write_text("steps:\n  - run: uv run pip-licenses\n")
        (tmp_path / "pyproject.toml").write_text(
            '[dependency-groups]\ndev = ["pip-licenses>=5.0.0"]\n'
        )
        assert check_pip_licenses_dep(tmp_path) == []

    def test_pip_licenses_missing_from_deps(self, tmp_path: Path) -> None:
        wf = tmp_path / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "ci.yml").write_text("steps:\n  - run: uv run pip-licenses\n")
        (tmp_path / "pyproject.toml").write_text('[dependency-groups]\ndev = ["pytest>=7.0"]\n')
        findings = check_pip_licenses_dep(tmp_path)
        assert len(findings) == 1
        assert findings[0]["status"] == "DRIFT"
        assert "pip-licenses" in findings[0]["message"]
        assert "uv add" in findings[0]["message"]

    def test_no_pyproject(self, tmp_path: Path) -> None:
        wf = tmp_path / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "ci.yml").write_text("steps:\n  - run: uv run pip-licenses\n")
        assert check_pip_licenses_dep(tmp_path) == []


# =========================================================================== #
# T10-4: Quality gate thresholds                                              #
# =========================================================================== #


class TestQualityGateThresholds:
    def test_no_workflows(self, tmp_path: Path) -> None:
        assert check_quality_gate_thresholds(tmp_path) == []

    def test_non_node_repo_skipped(self, tmp_path: Path) -> None:
        wf = tmp_path / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "ci.yml").write_text("run: eslint --max-warnings 100\n")
        # No package.json -> not a Node repo.
        assert check_quality_gate_thresholds(tmp_path) == []

    def test_node_repo_with_threshold(self, tmp_path: Path) -> None:
        wf = tmp_path / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "quality.yml").write_text("run: eslint --max-warnings 111 .\n")
        (tmp_path / "package.json").write_text("{}")
        findings = check_quality_gate_thresholds(tmp_path)
        assert len(findings) == 1
        assert findings[0]["status"] == "DRIFT"
        assert "111" in findings[0]["message"]

    def test_node_repo_without_threshold(self, tmp_path: Path) -> None:
        wf = tmp_path / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "quality.yml").write_text("run: eslint .\n")
        (tmp_path / "package.json").write_text("{}")
        assert check_quality_gate_thresholds(tmp_path) == []


# =========================================================================== #
# T12-2: delete_branch_on_merge                                              #
# =========================================================================== #


class TestDeleteBranchOnMerge:
    def test_no_long_lived_branches(self, tmp_path: Path) -> None:
        with patch(
            "augint_tools.standardize.checks._has_long_lived_branches",
            return_value=False,
        ):
            assert check_delete_branch_on_merge(tmp_path) == []

    def test_no_repo_slug(self, tmp_path: Path) -> None:
        with (
            patch(
                "augint_tools.standardize.checks._has_long_lived_branches",
                return_value=True,
            ),
            patch(
                "augint_tools.standardize.checks._get_repo_slug",
                return_value=None,
            ),
        ):
            assert check_delete_branch_on_merge(tmp_path) == []

    def test_setting_disabled(self, tmp_path: Path) -> None:
        with (
            patch(
                "augint_tools.standardize.checks._has_long_lived_branches",
                return_value=True,
            ),
            patch(
                "augint_tools.standardize.checks._get_repo_slug",
                return_value="owner/repo",
            ),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "false\n"
            assert check_delete_branch_on_merge(tmp_path) == []

    def test_setting_enabled(self, tmp_path: Path) -> None:
        with (
            patch(
                "augint_tools.standardize.checks._has_long_lived_branches",
                return_value=True,
            ),
            patch(
                "augint_tools.standardize.checks._get_repo_slug",
                return_value="owner/repo",
            ),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "true\n"
            findings = check_delete_branch_on_merge(tmp_path)
            assert len(findings) == 1
            assert findings[0]["status"] == "DRIFT"
            assert "delete_branch_on_merge" in findings[0]["message"]
            assert "gh api" in findings[0]["message"]

    def test_gh_unavailable(self, tmp_path: Path) -> None:
        with (
            patch(
                "augint_tools.standardize.checks._has_long_lived_branches",
                return_value=True,
            ),
            patch(
                "augint_tools.standardize.checks._get_repo_slug",
                return_value="owner/repo",
            ),
            patch("subprocess.run", side_effect=FileNotFoundError),
        ):
            assert check_delete_branch_on_merge(tmp_path) == []


# =========================================================================== #
# run_supplemental_checks dispatcher                                          #
# =========================================================================== #


class TestRunSupplementalChecks:
    def test_area_none_runs_all(self, tmp_path: Path) -> None:
        """area=None should run renovate, pipeline, precommit, and repo_settings checks."""
        with (
            patch(
                "augint_tools.standardize.checks.check_renovate_dual_config",
                return_value=[],
            ) as m1,
            patch(
                "augint_tools.standardize.checks.check_renovate_default_branch",
                return_value=[],
            ) as m2,
            patch(
                "augint_tools.standardize.checks.check_pip_licenses_dep",
                return_value=[],
            ) as m3,
            patch(
                "augint_tools.standardize.checks.check_quality_gate_thresholds",
                return_value=[],
            ) as m4,
            patch(
                "augint_tools.standardize.checks.check_ci_skip_keywords",
                return_value=[],
            ) as m5,
            patch(
                "augint_tools.standardize.checks.check_workflow_token_usage",
                return_value=[],
            ) as m6,
            patch(
                "augint_tools.standardize.checks.check_delete_branch_on_merge",
                return_value=[],
            ) as m7,
            patch(
                "augint_tools.standardize.checks.check_no_commit_to_branch_skip",
                return_value=[],
            ) as m8,
            patch(
                "augint_tools.standardize.checks.check_forbid_env_commit_exclusion",
                return_value=[],
            ) as m9,
        ):
            run_supplemental_checks(tmp_path, area=None)
            m1.assert_called_once()
            m2.assert_called_once()
            m3.assert_called_once()
            m4.assert_called_once()
            m5.assert_called_once()
            m6.assert_called_once()
            m7.assert_called_once()
            m8.assert_called_once()
            m9.assert_called_once()

    def test_area_renovate_only(self, tmp_path: Path) -> None:
        with (
            patch(
                "augint_tools.standardize.checks.check_renovate_dual_config",
                return_value=[],
            ) as m1,
            patch(
                "augint_tools.standardize.checks.check_renovate_default_branch",
                return_value=[],
            ) as m2,
            patch(
                "augint_tools.standardize.checks.check_ci_skip_keywords",
                return_value=[],
            ) as m3,
            patch(
                "augint_tools.standardize.checks.check_delete_branch_on_merge",
                return_value=[],
            ) as m4,
        ):
            run_supplemental_checks(tmp_path, area="renovate")
            m1.assert_called_once()
            m2.assert_called_once()
            m3.assert_not_called()
            m4.assert_not_called()

    def test_area_pipeline_only(self, tmp_path: Path) -> None:
        with (
            patch(
                "augint_tools.standardize.checks.check_renovate_dual_config",
                return_value=[],
            ) as m1,
            patch(
                "augint_tools.standardize.checks.check_ci_skip_keywords",
                return_value=[],
            ) as m2,
            patch(
                "augint_tools.standardize.checks.check_pip_licenses_dep",
                return_value=[],
            ) as m3,
            patch(
                "augint_tools.standardize.checks.check_delete_branch_on_merge",
                return_value=[],
            ) as m4,
        ):
            run_supplemental_checks(tmp_path, area="pipeline")
            m1.assert_not_called()
            m2.assert_called_once()
            m3.assert_called_once()
            m4.assert_not_called()


# =========================================================================== #
# Integration: supplemental checks in --verify flow                          #
# =========================================================================== #


_CLEAN_JSON = json.dumps(
    {
        "path": "/fake/path",
        "overall": "clean",
        "findings": [
            {"section": "detect", "status": "PASS", "message": "ok", "is_clean": True},
            {"section": "pipeline", "status": "PASS", "message": "ok", "is_clean": True},
        ],
    }
)


class TestVerifyIntegration:
    def test_supplemental_findings_merged_into_verify(self, tmp_path: Path, monkeypatch) -> None:
        """When supplemental checks find drift, the verify status should be drift."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()

        drift_finding = {
            "section": "renovate_config",
            "status": "DRIFT",
            "message": "dual config",
            "diff": None,
            "is_clean": False,
        }

        with (
            patch(
                "augint_tools.cli.commands.standardize._run_ai_shell",
                return_value=(0, _CLEAN_JSON, ""),
            ),
            patch(
                "augint_tools.cli.commands.standardize.run_supplemental_checks",
                return_value=[drift_finding],
            ),
        ):
            result = runner.invoke(cli, ["--json", "standardize", "--verify", str(tmp_path)])

        assert result.exit_code == 1, result.output
        data = json.loads(result.output)
        assert data["status"] == "drift"
        assert "2 pass, 1 drift" in data["summary"]
        # The supplemental finding should be in the result findings list.
        sections = [f["section"] for f in data["result"]["findings"]]
        assert "renovate_config" in sections

    def test_no_supplemental_findings_stays_clean(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()

        with (
            patch(
                "augint_tools.cli.commands.standardize._run_ai_shell",
                return_value=(0, _CLEAN_JSON, ""),
            ),
            patch(
                "augint_tools.cli.commands.standardize.run_supplemental_checks",
                return_value=[],
            ),
        ):
            result = runner.invoke(cli, ["--json", "standardize", "--verify", str(tmp_path)])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "ok"


class TestAreaIntegration:
    def test_supplemental_findings_downgrade_area_ok(self, tmp_path: Path, monkeypatch) -> None:
        """Area ok + supplemental findings -> drift."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()

        drift_finding = {
            "section": "pipeline_ci_skip",
            "status": "FAIL",
            "message": "skip ci found",
            "diff": None,
            "is_clean": False,
        }

        with (
            patch(
                "augint_tools.cli.commands.standardize._run_ai_shell",
                return_value=(0, "ok", ""),
            ),
            patch(
                "augint_tools.cli.commands.standardize.run_supplemental_checks",
                return_value=[drift_finding],
            ),
        ):
            result = runner.invoke(
                cli, ["--json", "standardize", "--area", "pipeline", str(tmp_path)]
            )

        assert result.exit_code == 1, result.output
        data = json.loads(result.output)
        assert data["status"] == "drift"
        assert "1 supplemental issue" in data["summary"]
        assert data["result"]["supplemental_findings"] == [drift_finding]

    def test_no_supplemental_area_stays_ok(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()

        with (
            patch(
                "augint_tools.cli.commands.standardize._run_ai_shell",
                return_value=(0, "ok", ""),
            ),
            patch(
                "augint_tools.cli.commands.standardize.run_supplemental_checks",
                return_value=[],
            ),
        ):
            result = runner.invoke(
                cli, ["--json", "standardize", "--area", "pipeline", str(tmp_path)]
            )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "ok"


# =========================================================================== #
# T13-2: Renovate formatting noise filter                                     #
# =========================================================================== #


class TestRenovateFormattingNoiseFilter:
    """T13-2: filter_renovate_formatting_noise detects JSON5 vs JSON noise."""

    def test_pure_formatting_diff_downgraded_to_pass(self) -> None:
        """A diff with only JSON5 formatting changes becomes PASS."""
        diff = textwrap.dedent("""\
            --- expected
            +++ /path/to/renovate.json5
            @@ -3,5 +3,3 @@
            -  "$schema": "https://docs.renovatebot.com/renovate-schema.json",
            -  "extends": [
            -    "config:recommended",
            -    "helpers:pinGitHubActionDigestsToSemver"
            -  ],
            +  $schema: 'https://docs.renovatebot.com/renovate-schema.json',
            +  extends: ['config:recommended', 'helpers:pinGitHubActionDigestsToSemver'],
        """).rstrip()

        findings = [
            {
                "section": "renovate",
                "status": "DRIFT",
                "message": "renovate.json5 differs",
                "diff": diff,
                "is_clean": False,
            }
        ]
        filter_renovate_formatting_noise(findings)

        assert findings[0]["status"] == "PASS"
        assert findings[0]["is_clean"] is True
        assert findings[0]["diff"] is None
        assert "formatting differences only" in findings[0]["message"]

    def test_mixed_diff_keeps_semantic_hunks(self) -> None:
        """A diff with both semantic and formatting changes keeps only semantic hunks."""
        diff = textwrap.dedent("""\
            --- expected
            +++ /path/to/renovate.json5
            @@ -1,2 +1,2 @@
            -// Renovate Bot Configuration -- service
            +// Renovate Bot Configuration -- IaC
            @@ -5,3 +5,2 @@
            -  "$schema": "https://docs.renovatebot.com/renovate-schema.json",
            -  "baseBranchPatterns": ["dev"],
            +  $schema: 'https://docs.renovatebot.com/renovate-schema.json',
            +  baseBranchPatterns: ['dev'],
        """).rstrip()

        findings = [
            {
                "section": "renovate",
                "status": "DRIFT",
                "message": "renovate.json5 differs",
                "diff": diff,
                "is_clean": False,
            }
        ]
        filter_renovate_formatting_noise(findings)

        assert findings[0]["status"] == "DRIFT"
        # The semantic hunk (comment change) is preserved.
        assert "service" in findings[0]["diff"]
        assert "IaC" in findings[0]["diff"]
        # The formatting hunk ($schema, baseBranchPatterns) is stripped.
        assert "$schema" not in findings[0]["diff"]
        assert "formatting-only hunks filtered" in findings[0]["message"]

    def test_no_diff_is_noop(self) -> None:
        """Findings without a diff are not modified."""
        findings = [
            {
                "section": "renovate",
                "status": "DRIFT",
                "message": "renovate.json5 missing",
                "diff": None,
                "is_clean": False,
            }
        ]
        filter_renovate_formatting_noise(findings)
        assert findings[0]["status"] == "DRIFT"
        assert findings[0]["diff"] is None

    def test_non_renovate_section_ignored(self) -> None:
        """Findings for other sections are not touched."""
        findings = [
            {
                "section": "pipeline",
                "status": "DRIFT",
                "message": "pipeline drifted",
                "diff": "--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new",
                "is_clean": False,
            }
        ]
        original_diff = findings[0]["diff"]
        filter_renovate_formatting_noise(findings)
        assert findings[0]["status"] == "DRIFT"
        assert findings[0]["diff"] == original_diff

    def test_semantic_only_diff_unchanged(self) -> None:
        """A diff with only semantic changes is not modified."""
        diff = textwrap.dedent("""\
            --- expected
            +++ /path/to/renovate.json5
            @@ -1,2 +1,2 @@
            -// Renovate Bot Configuration -- service
            +// Renovate Bot Configuration -- IaC
        """).rstrip()

        findings = [
            {
                "section": "renovate",
                "status": "DRIFT",
                "message": "renovate.json5 differs",
                "diff": diff,
                "is_clean": False,
            }
        ]
        filter_renovate_formatting_noise(findings)
        assert findings[0]["status"] == "DRIFT"
        assert findings[0]["diff"] == diff
        assert "formatting" not in findings[0]["message"]


# =========================================================================== #
# T13-8: no-commit-to-branch without pipeline SKIP                            #
# =========================================================================== #


class TestNoCommitToBranchSkip:
    def test_no_pre_commit_config(self, tmp_path: Path) -> None:
        assert check_no_commit_to_branch_skip(tmp_path) == []

    def test_no_hook_present(self, tmp_path: Path) -> None:
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []\n")
        assert check_no_commit_to_branch_skip(tmp_path) == []

    def test_hook_present_no_pipeline(self, tmp_path: Path) -> None:
        (tmp_path / ".pre-commit-config.yaml").write_text(
            "repos:\n  - hooks:\n    - id: no-commit-to-branch\n"
        )
        findings = check_no_commit_to_branch_skip(tmp_path)
        assert len(findings) == 1
        assert findings[0]["status"] == "DRIFT"
        assert "no-commit-to-branch" in findings[0]["message"]

    def test_hook_present_pipeline_without_skip(self, tmp_path: Path) -> None:
        (tmp_path / ".pre-commit-config.yaml").write_text(
            "repos:\n  - hooks:\n    - id: no-commit-to-branch\n"
        )
        wf = tmp_path / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "pipeline.yaml").write_text("name: CI\nsteps:\n  - run: pre-commit\n")
        findings = check_no_commit_to_branch_skip(tmp_path)
        assert len(findings) == 1
        assert findings[0]["status"] == "DRIFT"

    def test_hook_present_pipeline_with_skip(self, tmp_path: Path) -> None:
        (tmp_path / ".pre-commit-config.yaml").write_text(
            "repos:\n  - hooks:\n    - id: no-commit-to-branch\n"
        )
        wf = tmp_path / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "pipeline.yaml").write_text(
            "name: CI\nenv:\n  SKIP: no-commit-to-branch\nsteps:\n  - run: pre-commit\n"
        )
        assert check_no_commit_to_branch_skip(tmp_path) == []


# =========================================================================== #
# T13-2: forbid-env-commit without .env.example exclusion                     #
# =========================================================================== #


class TestForbidEnvCommitExclusion:
    def test_no_pre_commit_config(self, tmp_path: Path) -> None:
        assert check_forbid_env_commit_exclusion(tmp_path) == []

    def test_no_hook_present(self, tmp_path: Path) -> None:
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []\n")
        assert check_forbid_env_commit_exclusion(tmp_path) == []

    def test_hook_present_no_env_template(self, tmp_path: Path) -> None:
        (tmp_path / ".pre-commit-config.yaml").write_text(
            "repos:\n  - hooks:\n    - id: forbid-env-commit\n"
        )
        assert check_forbid_env_commit_exclusion(tmp_path) == []

    def test_hook_present_env_example_no_exclude(self, tmp_path: Path) -> None:
        (tmp_path / ".pre-commit-config.yaml").write_text(
            "repos:\n  - hooks:\n    - id: forbid-env-commit\n"
        )
        (tmp_path / ".env.example").write_text("DB_URL=\n")
        findings = check_forbid_env_commit_exclusion(tmp_path)
        assert len(findings) == 1
        assert findings[0]["status"] == "DRIFT"
        assert "forbid-env-commit" in findings[0]["message"]
        assert "exclude" in findings[0]["message"]

    def test_hook_present_env_sample_no_exclude(self, tmp_path: Path) -> None:
        (tmp_path / ".pre-commit-config.yaml").write_text(
            "repos:\n  - hooks:\n    - id: forbid-env-commit\n"
        )
        (tmp_path / ".env.sample").write_text("DB_URL=\n")
        findings = check_forbid_env_commit_exclusion(tmp_path)
        assert len(findings) == 1

    def test_hook_present_with_exclude(self, tmp_path: Path) -> None:
        (tmp_path / ".pre-commit-config.yaml").write_text(
            "repos:\n  - hooks:\n    - id: forbid-env-commit\n"
            "      exclude: '\\.(example|sample)$'\n"
        )
        (tmp_path / ".env.example").write_text("DB_URL=\n")
        assert check_forbid_env_commit_exclusion(tmp_path) == []
