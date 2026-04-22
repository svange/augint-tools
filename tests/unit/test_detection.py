"""Tests for detection engine."""

from pathlib import Path
from unittest.mock import patch

from augint_tools.detection.commands import CommandPlan, resolve_command_plan
from augint_tools.detection.engine import GitHubState, RepoContext, detect
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

    def test_nextjs_via_config_ts(self, tmp_path):
        (tmp_path / "next.config.ts").touch()
        assert detect_framework(tmp_path) == "nextjs"

    def test_nextjs_via_package_json(self, tmp_path):
        (tmp_path / "package.json").write_text('{"dependencies":{"next":"16.0.0"}}')
        assert detect_framework(tmp_path) == "nextjs"

    def test_nextjs_via_dev_dependency(self, tmp_path):
        (tmp_path / "package.json").write_text('{"devDependencies":{"next":"16.0.0"}}')
        assert detect_framework(tmp_path) == "nextjs"

    def test_vite(self, tmp_path):
        (tmp_path / "vite.config.ts").touch()
        assert detect_framework(tmp_path) == "vite"

    def test_nextjs_before_vite(self, tmp_path):
        """Next.js should win when both markers exist."""
        (tmp_path / "next.config.ts").touch()
        (tmp_path / "vite.config.ts").touch()
        assert detect_framework(tmp_path) == "nextjs"

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

    def test_nextjs_defaults(self):
        toolchain = ToolchainInfo(package_manager="npm", has_npm=True)
        plan = resolve_command_plan(toolchain, "typescript", "nextjs")
        assert plan.quality == "npx next lint"
        assert plan.tests == "npm test"
        assert plan.build == "npx next build"


class TestDetectEngine:
    def _toolchain(self) -> ToolchainInfo:
        return ToolchainInfo(
            package_manager="uv",
            has_pre_commit=True,
            has_pytest=True,
            has_ruff=True,
            has_mypy=True,
        )

    def _plan(self) -> CommandPlan:
        return CommandPlan(quality="q", tests="t", security="s", licenses="l", build="b")

    def test_detect_not_a_git_repo_defaults(self, tmp_path):
        with (
            patch("augint_tools.detection.engine.detect_language", return_value="python"),
            patch("augint_tools.detection.engine.detect_framework", return_value="plain"),
            patch("augint_tools.detection.engine.detect_toolchain", return_value=self._toolchain()),
            patch("augint_tools.detection.engine.resolve_command_plan", return_value=self._plan()),
            patch("augint_tools.detection.engine.is_git_repo", return_value=False),
            patch("augint_tools.detection.engine.is_gh_available", return_value=False),
        ):
            ctx = detect(tmp_path)
        assert isinstance(ctx, RepoContext)
        assert ctx.language == "python"
        assert ctx.default_branch == "main"
        assert ctx.current_branch is None
        assert ctx.target_pr_branch == "main"
        assert ctx.github == GitHubState()

    def test_detect_git_without_gh(self, tmp_path):
        with (
            patch("augint_tools.detection.engine.detect_language", return_value="python"),
            patch("augint_tools.detection.engine.detect_framework", return_value="plain"),
            patch("augint_tools.detection.engine.detect_toolchain", return_value=self._toolchain()),
            patch("augint_tools.detection.engine.resolve_command_plan", return_value=self._plan()),
            patch("augint_tools.detection.engine.is_git_repo", return_value=True),
            patch("augint_tools.detection.engine.detect_base_branch", return_value="dev"),
            patch("augint_tools.detection.engine.get_current_branch", return_value="feat/x"),
            patch("augint_tools.detection.engine.is_gh_available", return_value=False),
        ):
            ctx = detect(tmp_path)
        assert ctx.default_branch == "dev"
        assert ctx.current_branch == "feat/x"
        assert ctx.github.available is False
        assert ctx.github.repo_slug is None

    def test_detect_full_gh_path(self, tmp_path):
        with (
            patch("augint_tools.detection.engine.detect_language", return_value="python"),
            patch("augint_tools.detection.engine.detect_framework", return_value="plain"),
            patch("augint_tools.detection.engine.detect_toolchain", return_value=self._toolchain()),
            patch("augint_tools.detection.engine.resolve_command_plan", return_value=self._plan()),
            patch("augint_tools.detection.engine.is_git_repo", return_value=True),
            patch("augint_tools.detection.engine.detect_base_branch", return_value="main"),
            patch("augint_tools.detection.engine.get_current_branch", return_value="main"),
            patch("augint_tools.detection.engine.is_gh_available", return_value=True),
            patch("augint_tools.detection.engine.is_gh_authenticated", return_value=True),
            patch(
                "augint_tools.detection.engine.get_remote_url",
                return_value="https://github.com/org/repo.git",
            ),
        ):
            ctx = detect(tmp_path)
        assert ctx.github.available is True
        assert ctx.github.authenticated is True
        assert ctx.github.repo_slug == "org/repo"

    def test_detect_defaults_to_cwd(self):
        with (
            patch("augint_tools.detection.engine.detect_language", return_value="unknown"),
            patch("augint_tools.detection.engine.detect_framework", return_value="plain"),
            patch("augint_tools.detection.engine.detect_toolchain", return_value=self._toolchain()),
            patch("augint_tools.detection.engine.resolve_command_plan", return_value=self._plan()),
            patch("augint_tools.detection.engine.is_git_repo", return_value=False),
            patch("augint_tools.detection.engine.is_gh_available", return_value=False),
        ):
            ctx = detect()
        assert ctx.path == Path.cwd()

    def test_to_dict_round_trip(self, tmp_path):
        ctx = RepoContext(
            language="python",
            framework="sam",
            default_branch="main",
            current_branch="feat/x",
            target_pr_branch="main",
            toolchain=self._toolchain(),
            command_plan=self._plan(),
            github=GitHubState(available=True, authenticated=True, repo_slug="org/r"),
            path=tmp_path,
        )
        d = ctx.to_dict()
        assert d["language"] == "python"
        assert d["toolchain"]["package_manager"] == "uv"
        assert d["command_plan"]["quality"] == "q"
        assert d["github"] == {"available": True, "authenticated": True, "repo_slug": "org/r"}
        assert d["path"] == str(tmp_path)
