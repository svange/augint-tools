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
        assert "gh" in result.output
        assert "workspace" in result.output

    def test_global_flags_in_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert "--json" in result.output
        assert "--actionable" in result.output
        assert "--summary" in result.output

    def test_triage_command(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["triage", "--help"])
        assert result.exit_code == 0
        assert "--fix" in result.output
        assert "--run-id" in result.output

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
