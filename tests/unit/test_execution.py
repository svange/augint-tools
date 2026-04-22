"""Tests for execution module."""

import subprocess
from unittest.mock import Mock, patch

from augint_tools.execution import run_command
from augint_tools.execution.runner import discover_lint_command, discover_test_command


class TestCommandRunner:
    @patch("augint_tools.execution.runner.subprocess.run")
    def test_run_command_success(self, mock_run):
        """Test running a successful command."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout="Test output",
            stderr="",
        )

        result = run_command("echo test")
        assert result.success is True
        assert result.exit_code == 0
        assert result.stdout == "Test output"

    @patch("augint_tools.execution.runner.subprocess.run")
    def test_run_command_failure(self, mock_run):
        """Test running a failed command."""
        mock_run.return_value = Mock(
            returncode=1,
            stdout="",
            stderr="Error message",
        )

        result = run_command("false")
        assert result.success is False
        assert result.exit_code == 1
        assert result.stderr == "Error message"


class TestCommandDiscovery:
    def test_discover_test_command_pytest(self, tmp_path):
        """Test discovering pytest test command."""
        # Create pytest.ini
        (tmp_path / "pytest.ini").touch()

        cmd = discover_test_command(tmp_path)
        assert cmd == "pytest -v"

    def test_discover_test_command_none(self, tmp_path):
        """Test when no test command found."""
        cmd = discover_test_command(tmp_path)
        assert cmd is None

    def test_discover_lint_command_precommit(self, tmp_path):
        """Test discovering pre-commit lint command."""
        # Create .pre-commit-config.yaml
        (tmp_path / ".pre-commit-config.yaml").touch()

        cmd = discover_lint_command(tmp_path)
        assert cmd == "pre-commit run --all-files"

    def test_discover_lint_command_ruff(self, tmp_path):
        """Test discovering ruff lint command."""
        # Create pyproject.toml
        (tmp_path / "pyproject.toml").touch()

        # Pre-commit takes precedence, so only create pyproject.toml
        cmd = discover_lint_command(tmp_path)
        assert cmd == "ruff check ."

    def test_discover_lint_command_none(self, tmp_path):
        """Test when no lint command found."""
        cmd = discover_lint_command(tmp_path)
        assert cmd is None

    def test_discover_test_command_pyproject(self, tmp_path):
        (tmp_path / "pyproject.toml").touch()
        assert discover_test_command(tmp_path) == "pytest -v"

    def test_discover_test_command_npm(self, tmp_path):
        (tmp_path / "package.json").touch()
        assert discover_test_command(tmp_path) == "npm test"

    def test_discover_test_command_make(self, tmp_path):
        (tmp_path / "Makefile").touch()
        assert discover_test_command(tmp_path) == "make test"

    def test_discover_test_command_defaults_to_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert discover_test_command() is None

    def test_discover_lint_command_npm(self, tmp_path):
        (tmp_path / "package.json").touch()
        assert discover_lint_command(tmp_path) == "npm run lint"

    def test_discover_lint_command_make(self, tmp_path):
        (tmp_path / "Makefile").touch()
        assert discover_lint_command(tmp_path) == "make lint"

    def test_discover_lint_command_defaults_to_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert discover_lint_command() is None


class TestRunCommandFailurePaths:
    def test_timeout_returns_failure_with_message(self):
        with patch(
            "augint_tools.execution.runner.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="x", timeout=5),
        ):
            result = run_command("sleep 10", timeout=5)
        assert result.success is False
        assert result.exit_code == -1
        assert "timed out" in result.stderr

    def test_generic_exception_returns_failure(self):
        with patch(
            "augint_tools.execution.runner.subprocess.run",
            side_effect=OSError("permission denied"),
        ):
            result = run_command("x")
        assert result.success is False
        assert result.exit_code == -1
        assert "permission denied" in result.stderr
