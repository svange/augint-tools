"""Tests for check system."""

from pathlib import Path
from unittest.mock import patch

from augint_tools.checks import Phase, PhaseResult, resolve_plan, run_plan
from augint_tools.checks.runner import _apply_fix_flag, _extract_failures
from augint_tools.detection.commands import CommandPlan
from augint_tools.execution.runner import CommandResult


class TestPhases:
    def test_phase_values(self):
        assert Phase.QUALITY.value == "quality"
        assert Phase.TESTS.value == "tests"
        assert Phase.SECURITY.value == "security"
        assert Phase.LICENSES.value == "licenses"
        assert Phase.BUILD.value == "build"


class TestPlanResolution:
    def _sample_plan(self):
        return CommandPlan(
            quality="ruff check .",
            tests="pytest -v",
            security="pip-audit",
            licenses="pip-licenses",
            build="uv build",
        )

    def test_quick_preset(self):
        plan = resolve_plan(self._sample_plan(), preset="quick")
        assert plan.preset == "quick"
        assert len(plan.phases) == 1
        assert plan.phases[0].name == "quality"
        assert plan.phases[0].fixable is True

    def test_default_preset(self):
        plan = resolve_plan(self._sample_plan(), preset="default")
        assert len(plan.phases) == 2
        names = [p.name for p in plan.phases]
        assert names == ["quality", "tests"]

    def test_full_preset(self):
        plan = resolve_plan(self._sample_plan(), preset="full")
        assert len(plan.phases) == 5
        names = [p.name for p in plan.phases]
        assert names == ["quality", "security", "licenses", "tests", "build"]

    def test_skip(self):
        plan = resolve_plan(self._sample_plan(), preset="full", skip=["security", "licenses"])
        names = [p.name for p in plan.phases]
        assert "security" not in names
        assert "licenses" not in names
        assert "quality" in names
        assert "security" in plan.skipped
        assert "licenses" in plan.skipped

    def test_missing_command_skipped(self):
        plan_no_security = CommandPlan(
            quality="ruff check .",
            tests="pytest -v",
        )
        plan = resolve_plan(plan_no_security, preset="full")
        names = [p.name for p in plan.phases]
        assert "security" not in names
        assert "security" in plan.skipped

    def test_to_dict(self):
        plan = resolve_plan(self._sample_plan(), preset="quick")
        d = plan.to_dict()
        assert d["preset"] == "quick"
        assert len(d["phases"]) == 1
        assert d["phases"][0]["name"] == "quality"
        assert d["phases"][0]["command"] == "ruff check ."

    def test_ci_preset_mirrors_full(self):
        plan_ci = resolve_plan(self._sample_plan(), preset="ci")
        plan_full = resolve_plan(self._sample_plan(), preset="full")
        assert [p.name for p in plan_ci.phases] == [p.name for p in plan_full.phases]
        assert plan_ci.preset == "ci"

    def test_unknown_preset_falls_back_to_default(self):
        plan = resolve_plan(self._sample_plan(), preset="bogus")
        # Unknown preset silently uses default -> quality + tests.
        assert [p.name for p in plan.phases] == ["quality", "tests"]


class TestApplyFixFlag:
    def test_adds_fix_to_ruff(self):
        assert _apply_fix_flag("ruff check src/") == "ruff check --fix src/"

    def test_pre_commit_unchanged(self):
        # pre-commit auto-fixes already; command stays identical.
        assert _apply_fix_flag("pre-commit run --all-files") == "pre-commit run --all-files"

    def test_biome_check_gets_apply(self):
        assert _apply_fix_flag("biome check src/") == "biome check --apply src/"

    def test_unknown_tool_returns_unchanged(self):
        assert _apply_fix_flag("mypy src/") == "mypy src/"


class TestExtractFailures:
    def test_keeps_error_lines_only(self):
        output = "  starting build\nerror: something broke\nok: fine\nTask failed horribly\n"
        assert _extract_failures(output) == [
            "error: something broke",
            "Task failed horribly",
        ]

    def test_ignores_blank_lines(self):
        assert _extract_failures("\n\n  \n") == []

    def test_caps_at_twenty(self):
        spam = "\n".join([f"error line {i}" for i in range(50)])
        assert len(_extract_failures(spam)) == 20


class TestRunPlan:
    def _plan(self, *, fixable: bool = False) -> CommandPlan:
        return CommandPlan(quality="ruff check .", tests="pytest -v")

    def test_successful_run_marks_phases_passed(self):
        plan = resolve_plan(self._plan(), preset="default")
        with patch(
            "augint_tools.checks.runner.run_command",
            return_value=CommandResult(
                success=True, exit_code=0, stdout="ok", stderr="", command="x"
            ),
        ):
            results = run_plan(plan, cwd=Path("."))
        assert [r.status for r in results] == ["passed", "passed"]
        assert all(isinstance(r, PhaseResult) for r in results)
        assert results[0].to_dict()["status"] == "passed"

    def test_failure_extracts_failures_and_sets_status(self):
        plan = resolve_plan(self._plan(), preset="quick")
        failing = CommandResult(
            success=False,
            exit_code=1,
            stdout="",
            stderr="error: broken\nbuild failed\n",
            command="x",
        )
        with patch("augint_tools.checks.runner.run_command", return_value=failing):
            results = run_plan(plan, cwd=Path("."))
        assert results[0].status == "failed"
        assert "error: broken" in results[0].failures
        assert results[0].exit_code == 1

    def test_fix_mode_rewrites_command_and_marks_fixed(self):
        plan = resolve_plan(self._plan(), preset="quick")
        with patch(
            "augint_tools.checks.runner.run_command",
            return_value=CommandResult(
                success=True, exit_code=0, stdout="", stderr="", command="x"
            ),
        ) as mock_run:
            results = run_plan(plan, cwd=Path("."), fix=True)
        # The resolved phase for "quality" is fixable -> command gets --fix.
        assert mock_run.call_args.args[0] == "ruff check --fix ."
        assert results[0].status == "fixed"
        # The PhaseResult records the original command, not the fix-mutated one.
        assert results[0].command == "ruff check ."

    def test_failure_uses_stdout_when_stderr_empty(self):
        plan = resolve_plan(self._plan(), preset="quick")
        failing = CommandResult(
            success=False, exit_code=2, stdout="error: boom", stderr="", command="x"
        )
        with patch("augint_tools.checks.runner.run_command", return_value=failing):
            results = run_plan(plan, cwd=Path("."))
        assert "error: boom" in results[0].failures
