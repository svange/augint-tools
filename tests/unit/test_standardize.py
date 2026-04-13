"""Tests for the top-level `ai-tools standardize` command."""

from __future__ import annotations

import json
from unittest.mock import patch

from click.testing import CliRunner

from augint_tools.cli.__main__ import cli

_CLEAN_JSON = json.dumps(
    {
        "path": "/fake/path",
        # ai-shell emits "clean" here, not "pass" — see T10-1.
        "overall": "clean",
        "findings": [
            {
                "section": "detect",
                "status": "PASS",
                "message": "python/library",
                "diff": None,
                "is_clean": True,
            },
            {
                "section": "pipeline",
                "status": "PASS",
                "message": "all jobs present",
                "diff": None,
                "is_clean": True,
            },
        ],
    }
)

_DRIFT_JSON = json.dumps(
    {
        "path": "/fake/path",
        "overall": "drift",
        "findings": [
            {
                "section": "detect",
                "status": "PASS",
                "message": "python/library",
                "diff": None,
                "is_clean": True,
            },
            {
                "section": "pipeline",
                "status": "DRIFT",
                "message": "missing: Code quality",
                "diff": None,
                "is_clean": False,
            },
            {
                "section": "renovate",
                "status": "FAIL",
                "message": "renovate.json5 missing",
                "diff": None,
                "is_clean": False,
            },
        ],
    }
)


class TestStandardizeVerify:
    def test_clean_passes(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        with patch(
            "augint_tools.cli.commands.standardize._run_ai_shell",
            return_value=(0, _CLEAN_JSON, ""),
        ):
            result = runner.invoke(cli, ["--json", "standardize", "--verify", str(tmp_path)])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["command"] == "standardize --verify"
        assert data["scope"] == "repo"
        assert data["status"] == "ok"
        assert data["result"]["overall"] == "clean"
        assert data["next_actions"] == []

    def test_clean_with_nonzero_rc_still_ok(self, tmp_path, monkeypatch):
        """T10-1 regression: rc != 0 with clean findings must still report ok.

        ai-shell can return a non-zero exit code when a venv downgrade
        warning leaks to stderr, even on clean repos. The counts are the
        single source of truth.
        """
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        with patch(
            "augint_tools.cli.commands.standardize._run_ai_shell",
            return_value=(1, _CLEAN_JSON, "warning: venv downgrade detected"),
        ):
            result = runner.invoke(cli, ["--json", "standardize", "--verify", str(tmp_path)])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["status"] == "ok"
        assert "2 pass, 0 drift, 0 fail" in data["summary"]

    def test_clean_with_unknown_overall_still_ok(self, tmp_path, monkeypatch):
        """T10-1 regression: the `overall` string is ignored — counts rule.

        Whether ai-shell emits `overall="clean"`, `"pass"`, or something
        unexpected, a findings list with only PASS entries means status=ok.
        """
        monkeypatch.chdir(tmp_path)
        weird_clean = json.dumps(
            {
                "path": "/fake",
                "overall": "some-future-sentinel",
                "findings": [
                    {"section": "detect", "status": "PASS", "message": "", "is_clean": True},
                    {"section": "pipeline", "status": "PASS", "message": "", "is_clean": True},
                ],
            }
        )
        runner = CliRunner()
        with patch(
            "augint_tools.cli.commands.standardize._run_ai_shell",
            return_value=(0, weird_clean, ""),
        ):
            result = runner.invoke(cli, ["--json", "standardize", "--verify", str(tmp_path)])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["status"] == "ok"

    def test_empty_findings_is_error(self, tmp_path, monkeypatch):
        """Parseable JSON with zero findings must not silently claim clean."""
        monkeypatch.chdir(tmp_path)
        empty = json.dumps({"path": "/fake", "overall": "clean", "findings": []})
        runner = CliRunner()
        with patch(
            "augint_tools.cli.commands.standardize._run_ai_shell",
            return_value=(0, empty, ""),
        ):
            result = runner.invoke(cli, ["--json", "standardize", "--verify", str(tmp_path)])

        assert result.exit_code == 2
        data = json.loads(result.output)
        assert data["status"] == "error"
        assert "zero findings" in data["summary"]

    def test_drift_exits_1(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        with patch(
            "augint_tools.cli.commands.standardize._run_ai_shell",
            return_value=(1, _DRIFT_JSON, ""),
        ):
            result = runner.invoke(cli, ["--json", "standardize", "--verify", str(tmp_path)])

        assert result.exit_code == 1, result.output
        data = json.loads(result.output)
        assert data["status"] == "drift"
        assert "1 drift" in data["summary"]
        assert "1 fail" in data["summary"]
        assert any("ai-standardize-repo" in a for a in data["next_actions"])

    def test_spawn_failure_exits_2(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        with patch(
            "augint_tools.cli.commands.standardize._run_ai_shell",
            return_value=(-1, "", "ai-shell executable not found on PATH"),
        ):
            result = runner.invoke(cli, ["--json", "standardize", "--verify", str(tmp_path)])

        assert result.exit_code == 2, result.output
        data = json.loads(result.output)
        assert data["status"] == "error"
        assert "not found" in data["summary"]

    def test_empty_stdout_is_error(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        with patch(
            "augint_tools.cli.commands.standardize._run_ai_shell",
            return_value=(0, "", "stderr noise"),
        ):
            result = runner.invoke(cli, ["--json", "standardize", "--verify", str(tmp_path)])

        assert result.exit_code == 2
        data = json.loads(result.output)
        assert data["status"] == "error"
        assert "noise" in data["summary"] or "no output" in data["summary"]

    def test_invalid_json_is_error(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        with patch(
            "augint_tools.cli.commands.standardize._run_ai_shell",
            return_value=(0, "not valid json", ""),
        ):
            result = runner.invoke(cli, ["--json", "standardize", "--verify", str(tmp_path)])

        assert result.exit_code == 2
        data = json.loads(result.output)
        assert data["status"] == "error"
        assert "parse" in data["summary"].lower()

    def test_missing_findings_is_error(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        with patch(
            "augint_tools.cli.commands.standardize._run_ai_shell",
            return_value=(0, json.dumps({"overall": "pass"}), ""),
        ):
            result = runner.invoke(cli, ["--json", "standardize", "--verify", str(tmp_path)])

        assert result.exit_code == 2
        data = json.loads(result.output)
        assert data["status"] == "error"
        assert "findings" in data["summary"]

    def test_verify_rejects_dry_run(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli, ["--json", "standardize", "--verify", "--dry-run", str(tmp_path)]
        )
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["status"] == "error"
        assert "dry-run" in data["summary"].lower()

    def test_verify_passes_path_as_argument(self, tmp_path, monkeypatch):
        """The command must pass PATH as an argument, not cd into it."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        captured: dict = {}

        def fake_run(cmd: list[str]) -> tuple[int, str, str]:
            captured["cmd"] = cmd
            return 0, _CLEAN_JSON, ""

        with patch(
            "augint_tools.cli.commands.standardize._run_ai_shell",
            side_effect=fake_run,
        ):
            runner.invoke(cli, ["--json", "standardize", "--verify", str(tmp_path)])

        assert captured["cmd"][0] == "ai-shell"
        assert "standardize" in captured["cmd"]
        assert "repo" in captured["cmd"]
        assert "--verify" in captured["cmd"]
        assert "--json" in captured["cmd"]
        assert str(tmp_path.resolve()) in captured["cmd"]

    def test_verify_defaults_to_cwd(self, tmp_path, monkeypatch):
        """When PATH is omitted, the command resolves to the current directory."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        captured: dict = {}

        def fake_run(cmd: list[str]) -> tuple[int, str, str]:
            captured["cmd"] = cmd
            return 0, _CLEAN_JSON, ""

        with patch(
            "augint_tools.cli.commands.standardize._run_ai_shell",
            side_effect=fake_run,
        ):
            runner.invoke(cli, ["--json", "standardize", "--verify"])

        assert str(tmp_path.resolve()) in captured["cmd"]


class TestStandardizeArea:
    def test_pipeline_uses_validate(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        captured: dict = {}

        def fake_run(cmd: list[str]) -> tuple[int, str, str]:
            captured["cmd"] = cmd
            return 0, "ok", ""

        with patch(
            "augint_tools.cli.commands.standardize._run_ai_shell",
            side_effect=fake_run,
        ):
            result = runner.invoke(
                cli, ["--json", "standardize", "--area", "pipeline", str(tmp_path)]
            )

        assert result.exit_code == 0, result.output
        assert "--validate" in captured["cmd"]
        # --json propagated only because the user passed --json globally
        assert "--json" in captured["cmd"]

    def test_pipeline_accepts_verify_flag(self, tmp_path, monkeypatch):
        """T10-2: `--area pipeline --verify` must be accepted; pipeline is
        already read-only so the combo is a no-op hint."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        captured: dict = {}

        def fake_run(cmd: list[str]) -> tuple[int, str, str]:
            captured["cmd"] = cmd
            return 0, "ok", ""

        with patch(
            "augint_tools.cli.commands.standardize._run_ai_shell",
            side_effect=fake_run,
        ):
            result = runner.invoke(
                cli,
                [
                    "--json",
                    "standardize",
                    "--area",
                    "pipeline",
                    "--verify",
                    str(tmp_path),
                ],
            )

        assert result.exit_code == 0, result.output
        # Still delegates to the same ai-shell invocation as --area pipeline alone.
        assert "--validate" in captured["cmd"]
        assert captured["cmd"][:3] == ["ai-shell", "standardize", "pipeline"]

    def test_non_pipeline_area_with_verify_errors(self, tmp_path, monkeypatch):
        """T10-2: --verify is only valid with --area pipeline. Other areas
        have no read-only mode and must error out explicitly."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--json",
                "standardize",
                "--area",
                "precommit",
                "--verify",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["status"] == "error"
        assert "pipeline" in data["summary"]
        assert "precommit" in data["summary"]

    def test_pipeline_drift_exits_1(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        with patch(
            "augint_tools.cli.commands.standardize._run_ai_shell",
            return_value=(1, '{"drift": true}', ""),
        ):
            result = runner.invoke(
                cli, ["--json", "standardize", "--area", "pipeline", str(tmp_path)]
            )

        assert result.exit_code == 1, result.output
        data = json.loads(result.output)
        assert data["status"] == "drift"

    def test_pipeline_dry_run_delegates_to_all(self, tmp_path, monkeypatch):
        """T13-1: --area pipeline --dry-run delegates to --all --dry-run."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        plan = [{"step": "pipeline", "status": "OK", "message": "spec clean"}]
        captured: dict = {}

        def fake_run(cmd: list[str]) -> tuple[int, str, str]:
            captured["cmd"] = cmd
            return 0, json.dumps(plan), ""

        with patch(
            "augint_tools.cli.commands.standardize._run_ai_shell",
            side_effect=fake_run,
        ):
            result = runner.invoke(
                cli,
                ["--json", "standardize", "--area", "pipeline", "--dry-run", str(tmp_path)],
            )

        assert result.exit_code == 0, result.output
        assert "--all" in captured["cmd"]
        assert "--dry-run" in captured["cmd"]
        assert "--json" in captured["cmd"]
        data = json.loads(result.output)
        assert data["command"] == "standardize --area pipeline --dry-run"
        assert data["result"]["step"] == plan[0]
        assert data["result"]["dry_run"] is True

    def test_precommit_runs(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        captured: dict = {}

        def fake_run(cmd: list[str]) -> tuple[int, str, str]:
            captured["cmd"] = cmd
            return 0, "wrote .pre-commit-config.yaml", ""

        with patch(
            "augint_tools.cli.commands.standardize._run_ai_shell",
            side_effect=fake_run,
        ):
            result = runner.invoke(
                cli, ["--json", "standardize", "--area", "precommit", str(tmp_path)]
            )

        assert result.exit_code == 0, result.output
        assert captured["cmd"] == [
            "ai-shell",
            "standardize",
            "precommit",
            str(tmp_path.resolve()),
        ]

    def test_precommit_dry_run_delegates_to_all(self, tmp_path, monkeypatch):
        """T13-1: --area precommit --dry-run delegates to --all --dry-run."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        plan = [
            {"step": "pipeline", "status": "OK", "message": "spec clean"},
            {"step": "precommit", "status": "DRIFT", "message": "would write 7 hooks"},
        ]
        captured: dict = {}

        def fake_run(cmd: list[str]) -> tuple[int, str, str]:
            captured["cmd"] = cmd
            return 0, json.dumps(plan), ""

        with patch(
            "augint_tools.cli.commands.standardize._run_ai_shell",
            side_effect=fake_run,
        ):
            result = runner.invoke(
                cli,
                ["--json", "standardize", "--area", "precommit", "--dry-run", str(tmp_path)],
            )

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["command"] == "standardize --area precommit --dry-run"
        assert data["result"]["step"] == plan[1]
        assert "would write 7 hooks" in data["summary"]

    def test_dotfiles_supports_dry_run(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        captured: dict = {}

        def fake_run(cmd: list[str]) -> tuple[int, str, str]:
            captured["cmd"] = cmd
            return 0, "would write .editorconfig", ""

        with patch(
            "augint_tools.cli.commands.standardize._run_ai_shell",
            side_effect=fake_run,
        ):
            result = runner.invoke(
                cli,
                ["--json", "standardize", "--area", "dotfiles", "--dry-run", str(tmp_path)],
            )

        assert result.exit_code == 0, result.output
        assert "--dry-run" in captured["cmd"]
        assert captured["cmd"][:3] == ["ai-shell", "standardize", "dotfiles"]

    def test_spawn_failure_exits_2(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        with patch(
            "augint_tools.cli.commands.standardize._run_ai_shell",
            return_value=(-1, "", "ai-shell executable not found on PATH"),
        ):
            result = runner.invoke(
                cli, ["--json", "standardize", "--area", "precommit", str(tmp_path)]
            )

        assert result.exit_code == 2
        data = json.loads(result.output)
        assert data["status"] == "error"


class TestStandardizeAll:
    def test_apply_mode(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        captured: dict = {}

        def fake_run(cmd: list[str]) -> tuple[int, str, str]:
            captured["cmd"] = cmd
            return 0, "wrote files", ""

        with patch(
            "augint_tools.cli.commands.standardize._run_ai_shell",
            side_effect=fake_run,
        ):
            result = runner.invoke(cli, ["--json", "standardize", "--all", str(tmp_path)])

        assert result.exit_code == 0, result.output
        assert captured["cmd"][:4] == ["ai-shell", "standardize", "repo", "--all"]
        # --json is not valid with plain --all, so we should NOT pass it through.
        assert "--json" not in captured["cmd"]
        assert "--dry-run" not in captured["cmd"]

    def test_dry_run_mode_passes_json(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        captured: dict = {}

        def fake_run(cmd: list[str]) -> tuple[int, str, str]:
            captured["cmd"] = cmd
            return 0, json.dumps({"plan": ["step1", "step2"]}), ""

        with patch(
            "augint_tools.cli.commands.standardize._run_ai_shell",
            side_effect=fake_run,
        ):
            result = runner.invoke(
                cli, ["--json", "standardize", "--all", "--dry-run", str(tmp_path)]
            )

        assert result.exit_code == 0, result.output
        assert "--dry-run" in captured["cmd"]
        # --json only valid with --all --dry-run, should be passed.
        assert "--json" in captured["cmd"]
        data = json.loads(result.output)
        assert data["result"]["dry_run"] is True
        assert data["result"]["plan"] == {"plan": ["step1", "step2"]}

    def test_failure_exits_2(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        with patch(
            "augint_tools.cli.commands.standardize._run_ai_shell",
            return_value=(3, "", "something exploded"),
        ):
            result = runner.invoke(cli, ["--json", "standardize", "--all", str(tmp_path)])

        assert result.exit_code == 2
        data = json.loads(result.output)
        assert data["status"] == "error"
        assert "exited 3" in data["summary"]


class TestStandardizeLocalJsonFlag:
    """Tests for the subcommand-level --json flag on standardize."""

    def test_local_json_flag_verify(self, tmp_path, monkeypatch):
        """``ai-tools standardize --json --verify`` produces JSON output."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        with patch(
            "augint_tools.cli.commands.standardize._run_ai_shell",
            return_value=(0, _CLEAN_JSON, ""),
        ):
            result = runner.invoke(cli, ["standardize", "--json", "--verify", str(tmp_path)])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["command"] == "standardize --verify"
        assert data["status"] == "ok"

    def test_both_global_and_local_json_flags(self, tmp_path, monkeypatch):
        """Both ``--json`` flags together must not crash and produce JSON."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        with patch(
            "augint_tools.cli.commands.standardize._run_ai_shell",
            return_value=(0, _CLEAN_JSON, ""),
        ):
            result = runner.invoke(
                cli, ["--json", "standardize", "--json", "--verify", str(tmp_path)]
            )

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["status"] == "ok"

    def test_local_json_flag_area_pipeline(self, tmp_path, monkeypatch):
        """Local ``--json`` propagates to the ai-shell subprocess for pipeline."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        captured: dict = {}

        def fake_run(cmd: list[str]) -> tuple[int, str, str]:
            captured["cmd"] = cmd
            return 0, "ok", ""

        with patch(
            "augint_tools.cli.commands.standardize._run_ai_shell",
            side_effect=fake_run,
        ):
            result = runner.invoke(
                cli, ["standardize", "--json", "--area", "pipeline", str(tmp_path)]
            )

        assert result.exit_code == 0, result.output
        assert "--json" in captured["cmd"]
        assert "--validate" in captured["cmd"]

    def test_local_json_flag_all_dry_run(self, tmp_path, monkeypatch):
        """Local ``--json`` propagates to ``--all --dry-run`` subprocess."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        captured: dict = {}

        def fake_run(cmd: list[str]) -> tuple[int, str, str]:
            captured["cmd"] = cmd
            return 0, json.dumps({"plan": ["step1"]}), ""

        with patch(
            "augint_tools.cli.commands.standardize._run_ai_shell",
            side_effect=fake_run,
        ):
            result = runner.invoke(
                cli, ["standardize", "--json", "--all", "--dry-run", str(tmp_path)]
            )

        assert result.exit_code == 0, result.output
        assert "--json" in captured["cmd"]
        assert "--dry-run" in captured["cmd"]
        data = json.loads(result.output)
        assert data["result"]["plan"] == {"plan": ["step1"]}


class TestAreaDryRun:
    """T13-1: --area <name> --dry-run extracts from --all --dry-run plan."""

    def test_step_not_found_includes_full_plan(self, tmp_path, monkeypatch):
        """When the area step isn't in the plan, the full plan is returned."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        plan = [{"step": "pipeline", "status": "OK", "message": "clean"}]

        with patch(
            "augint_tools.cli.commands.standardize._run_ai_shell",
            return_value=(0, json.dumps(plan), ""),
        ):
            result = runner.invoke(
                cli, ["--json", "standardize", "--area", "renovate", "--dry-run", str(tmp_path)]
            )

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert "step" not in data["result"]
        assert data["result"]["plan"] == plan

    def test_plan_as_dict_with_steps_key(self, tmp_path, monkeypatch):
        """Plan wrapped in a dict with a 'steps' key is handled."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        plan = {"steps": [{"step": "renovate", "status": "DRIFT", "message": "would update"}]}

        with patch(
            "augint_tools.cli.commands.standardize._run_ai_shell",
            return_value=(0, json.dumps(plan), ""),
        ):
            result = runner.invoke(
                cli, ["--json", "standardize", "--area", "renovate", "--dry-run", str(tmp_path)]
            )

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["result"]["step"] == plan["steps"][0]

    def test_spawn_failure_exits_2(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        with patch(
            "augint_tools.cli.commands.standardize._run_ai_shell",
            return_value=(-1, "", "ai-shell executable not found on PATH"),
        ):
            result = runner.invoke(
                cli, ["--json", "standardize", "--area", "pipeline", "--dry-run", str(tmp_path)]
            )

        assert result.exit_code == 2
        data = json.loads(result.output)
        assert data["status"] == "error"
        assert "not found" in data["summary"]

    def test_supplemental_downgrades_to_drift(self, tmp_path, monkeypatch):
        """Supplemental findings downgrade a clean dry-run to drift."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        plan = [{"step": "pipeline", "status": "OK", "message": "spec clean"}]
        fake_finding = [
            {"section": "pipeline_ci_skip", "status": "FAIL", "message": "skip ci found"}
        ]

        with (
            patch(
                "augint_tools.cli.commands.standardize._run_ai_shell",
                return_value=(0, json.dumps(plan), ""),
            ),
            patch(
                "augint_tools.cli.commands.standardize.run_supplemental_checks",
                return_value=fake_finding,
            ),
        ):
            result = runner.invoke(
                cli, ["--json", "standardize", "--area", "pipeline", "--dry-run", str(tmp_path)]
            )

        assert result.exit_code == 1, result.output
        data = json.loads(result.output)
        assert data["status"] == "drift"
        assert "1 supplemental issue found" in data["summary"]
        assert data["result"]["supplemental_findings"] == fake_finding

    def test_dotfiles_dry_run_still_uses_native(self, tmp_path, monkeypatch):
        """dotfiles --dry-run still delegates natively, not via --all."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        captured: dict = {}

        def fake_run(cmd: list[str]) -> tuple[int, str, str]:
            captured["cmd"] = cmd
            return 0, "would write .editorconfig", ""

        with patch(
            "augint_tools.cli.commands.standardize._run_ai_shell",
            side_effect=fake_run,
        ):
            result = runner.invoke(
                cli,
                ["--json", "standardize", "--area", "dotfiles", "--dry-run", str(tmp_path)],
            )

        assert result.exit_code == 0, result.output
        assert captured["cmd"][:3] == ["ai-shell", "standardize", "dotfiles"]
        assert "--dry-run" in captured["cmd"]
        assert "--all" not in captured["cmd"]


class TestAllSupplementalChecks:
    """T13-3: --all surfaces supplemental checks alongside ai-shell plan."""

    def test_dry_run_includes_supplemental_findings(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        fake_finding = [
            {"section": "pipeline_ci_skip", "status": "FAIL", "message": "skip ci found"}
        ]

        with (
            patch(
                "augint_tools.cli.commands.standardize._run_ai_shell",
                return_value=(0, json.dumps({"plan": ["step1"]}), ""),
            ),
            patch(
                "augint_tools.cli.commands.standardize.run_supplemental_checks",
                return_value=fake_finding,
            ),
        ):
            result = runner.invoke(
                cli, ["--json", "standardize", "--all", "--dry-run", str(tmp_path)]
            )

        assert result.exit_code == 1, result.output
        data = json.loads(result.output)
        assert data["status"] == "drift"
        assert data["result"]["supplemental_findings"] == fake_finding
        assert "not auto-fixable" in data["summary"]

    def test_apply_includes_supplemental_findings(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        fake_finding = [
            {"section": "pipeline_token_usage", "status": "DRIFT", "message": "wrong token"}
        ]

        with (
            patch(
                "augint_tools.cli.commands.standardize._run_ai_shell",
                return_value=(0, "wrote files", ""),
            ),
            patch(
                "augint_tools.cli.commands.standardize.run_supplemental_checks",
                return_value=fake_finding,
            ),
        ):
            result = runner.invoke(cli, ["--json", "standardize", "--all", str(tmp_path)])

        assert result.exit_code == 1, result.output
        data = json.loads(result.output)
        assert data["status"] == "drift"
        assert data["result"]["supplemental_findings"] == fake_finding

    def test_no_supplemental_stays_ok(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()

        with (
            patch(
                "augint_tools.cli.commands.standardize._run_ai_shell",
                return_value=(0, "wrote files", ""),
            ),
            patch(
                "augint_tools.cli.commands.standardize.run_supplemental_checks",
                return_value=[],
            ),
        ):
            result = runner.invoke(cli, ["--json", "standardize", "--all", str(tmp_path)])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["status"] == "ok"
        assert "supplemental_findings" not in data["result"]
