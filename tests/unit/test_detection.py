"""Tests for detection engine."""

from augint_tools.detection.commands import resolve_command_plan
from augint_tools.detection.framework import detect_framework
from augint_tools.detection.language import detect_language
from augint_tools.detection.toolchain import ToolchainInfo, detect_toolchain


class TestLanguageDetection:
    def test_python(self, tmp_path):
        (tmp_path / "pyproject.toml").touch()
        assert detect_language(tmp_path) == "python"

    def test_typescript(self, tmp_path):
        (tmp_path / "tsconfig.json").touch()
        assert detect_language(tmp_path) == "typescript"

    def test_javascript(self, tmp_path):
        (tmp_path / "package.json").touch()
        assert detect_language(tmp_path) == "typescript"  # JS treated as TS ecosystem

    def test_mixed(self, tmp_path):
        (tmp_path / "pyproject.toml").touch()
        (tmp_path / "package.json").touch()
        assert detect_language(tmp_path) == "mixed"

    def test_unknown(self, tmp_path):
        assert detect_language(tmp_path) == "unknown"


class TestFrameworkDetection:
    def test_sam(self, tmp_path):
        (tmp_path / "template.yaml").touch()
        assert detect_framework(tmp_path) == "sam"

    def test_cdk(self, tmp_path):
        (tmp_path / "cdk.json").touch()
        assert detect_framework(tmp_path) == "cdk"

    def test_terraform(self, tmp_path):
        (tmp_path / "main.tf").touch()
        assert detect_framework(tmp_path) == "terraform"

    def test_nextjs(self, tmp_path):
        (tmp_path / "next.config.js").touch()
        assert detect_framework(tmp_path) == "nextjs"

    def test_vite(self, tmp_path):
        (tmp_path / "vite.config.ts").touch()
        assert detect_framework(tmp_path) == "vite"

    def test_plain(self, tmp_path):
        assert detect_framework(tmp_path) == "plain"


class TestToolchainDetection:
    def test_uv_project(self, tmp_path):
        (tmp_path / "uv.lock").touch()
        (tmp_path / ".pre-commit-config.yaml").touch()
        (tmp_path / "pyproject.toml").touch()
        info = detect_toolchain(tmp_path)
        assert info.package_manager == "uv"
        assert info.has_pre_commit is True
        assert info.has_pytest is True
        assert info.has_ruff is True

    def test_npm_project(self, tmp_path):
        (tmp_path / "package-lock.json").touch()
        info = detect_toolchain(tmp_path)
        assert info.package_manager == "npm"


class TestCommandPlanResolution:
    def test_python_defaults(self):
        toolchain = ToolchainInfo(
            package_manager="uv",
            has_pre_commit=True,
            has_pytest=True,
        )
        plan = resolve_command_plan(toolchain, "python")
        assert plan.quality == "uv run pre-commit run --all-files"
        assert plan.tests == "uv run pytest -v"

    def test_typescript_defaults(self):
        toolchain = ToolchainInfo(package_manager="npm", has_npm=True)
        plan = resolve_command_plan(toolchain, "typescript")
        assert plan.quality == "npm run lint"
        assert plan.tests == "npm test"
        assert plan.build == "npm run build"
