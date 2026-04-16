"""Tests for env CLI command group integration."""

import json
from pathlib import Path

from click.testing import CliRunner

from augint_tools.cli.__main__ import cli


class TestEnvGroup:
    def test_env_in_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "env" in result.output

    def test_env_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["env", "--help"])
        assert result.exit_code == 0
        assert "classify" in result.output
        assert "sync" in result.output
        assert "chezmoi" in result.output


class TestClassifyCommand:
    def test_classify_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["env", "classify", "--help"])
        assert result.exit_code == 0
        assert "secret" in result.output.lower() or "classify" in result.output.lower()

    def test_classify_env_file(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".env").write_text(
                "APP_NAME=myapp\nGH_TOKEN=ghp_abc123\nDB_HOST=localhost\nAWS_PROFILE=default\n"
            )
            result = runner.invoke(cli, ["env", "classify"])
        assert result.exit_code == 0
        assert "1 secrets" in result.output
        assert "2 variables" in result.output
        assert "1 skipped" in result.output

    def test_classify_json_output(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".env").write_text("APP_NAME=myapp\nGH_TOKEN=ghp_abc123\n")
            result = runner.invoke(cli, ["--json", "env", "classify"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "ok"
        assert len(data["result"]["secrets"]) == 1
        assert data["result"]["secrets"][0]["key"] == "GH_TOKEN"

    def test_classify_missing_file(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(cli, ["env", "classify", "nonexistent.env"])
        assert result.exit_code != 0

    def test_classify_empty_file(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".env").write_text("")
            result = runner.invoke(cli, ["env", "classify"])
        assert result.exit_code == 0
        assert "0 secrets" in result.output


class TestSyncCommand:
    def test_sync_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["env", "sync", "--help"])
        assert result.exit_code == 0
        assert "--dry-run" in result.output

    def test_sync_missing_file(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(cli, ["env", "sync", "nonexistent.env"])
        assert result.exit_code != 0
