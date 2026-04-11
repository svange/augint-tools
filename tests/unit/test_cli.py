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
        assert "standardize" in result.output

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
        assert "promote" in result.output
        assert "rollback" in result.output
        assert "health" in result.output

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

    def test_standardize_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["standardize", "--help"])
        assert result.exit_code == 0
        assert "--verify" in result.output
        assert "--area" in result.output
        assert "--all" in result.output
        assert "--dry-run" in result.output

    def test_standardize_requires_mode(self, tmp_path, monkeypatch):
        """No flag given -> error asking for --verify/--area/--all."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "standardize"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["status"] == "error"
        assert "--verify" in data["summary"]

    def test_standardize_mutual_exclusion(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "standardize", "--verify", "--all"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["status"] == "error"
        assert "mutually exclusive" in data["summary"].lower()

    def test_standardize_missing_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "standardize", str(tmp_path / "nope"), "--verify"])
        assert result.exit_code == 2
        data = json.loads(result.output)
        assert data["status"] == "error"
        assert "does not exist" in data["summary"].lower()

    def test_stub_commands(self):
        """Test that P1/P2 stubs emit the right output."""
        runner = CliRunner()
        stubs = [
            ["repo", "promote"],
            ["repo", "rollback", "plan"],
            ["repo", "rollback", "apply"],
            ["repo", "health"],
            ["workspace", "graph"],
            ["workspace", "update"],
        ]
        for args in stubs:
            result = runner.invoke(cli, args)
            assert "not yet implemented" in result.output.lower(), f"Stub failed for {args}"
