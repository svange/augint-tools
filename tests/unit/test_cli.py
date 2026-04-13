"""Tests for CLI module."""

import json

from click.testing import CliRunner

from augint_tools.cli.__main__ import cli


class TestCli:
    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "CLI for AI-assisted repository and workspace workflows." in result.output
        assert "init" in result.output
        assert "repo" in result.output
        assert "workspace" in result.output

    def test_global_flags_in_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert "--json" in result.output
        assert "--actionable" in result.output
        assert "--summary" in result.output

    def test_repo_subgroups(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["repo", "--help"])
        assert result.exit_code == 0
        assert "inspect" in result.output
        assert "status" in result.output
        assert "issues" in result.output
        assert "branch" in result.output
        assert "check" in result.output
        assert "submit" in result.output
        assert "ci" in result.output

    def test_repo_inspect(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["repo", "inspect"])
        assert result.exit_code == 0
        assert "python" in result.output.lower()

    def test_repo_inspect_json(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "repo", "inspect"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["command"] == "repo inspect"
        assert data["scope"] == "repo"
        assert data["status"] == "ok"
        assert "result" in data
        assert data["result"]["language"] == "python"

    def test_repo_status(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["repo", "status"])
        assert result.exit_code == 0
        assert "status" in result.output.lower()

    def test_repo_status_json(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "repo", "status"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["command"] == "repo status"
        assert data["scope"] == "repo"
        assert "result" in data
        assert "repo" in data["result"]

    def test_repo_check_plan(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["repo", "check", "plan"])
        assert result.exit_code == 0
        assert "phases" in result.output.lower() or "preset" in result.output.lower()

    def test_repo_check_plan_json(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "repo", "check", "plan", "--preset", "full"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["result"]["preset"] == "full"
        assert len(data["result"]["phases"]) > 0

    def test_workspace_subgroups(self):
        """Test that workspace subcommands are available."""
        runner = CliRunner()
        result = runner.invoke(cli, ["workspace", "--help"])
        assert result.exit_code == 0
        assert "inspect" in result.output

    def test_workspace_status_no_config(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["workspace", "status"])
        assert result.exit_code == 1

    def test_workspace_status_json_no_config(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "workspace", "status"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["status"] == "error"
