"""Tests for check system."""

from augint_tools.checks import Phase, resolve_plan
from augint_tools.detection.commands import CommandPlan


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
