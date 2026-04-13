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
        assert "status" in result.output
        assert "branch" in result.output
        assert "submit" in result.output
        assert "ci" in result.output

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
