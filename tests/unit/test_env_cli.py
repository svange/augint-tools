"""Tests for gh CLI command group integration."""

import json
from pathlib import Path

from click.testing import CliRunner

from augint_tools.cli.__main__ import cli


class TestGhGroup:
    def test_gh_in_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "gh" in result.output

    def test_gh_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["gh", "--help"])
        assert result.exit_code == 0
        assert "classify" in result.output
        assert "push" in result.output

    def test_sync_in_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "sync" in result.output


class TestClassifyCommand:
    def test_classify_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["gh", "classify", "--help"])
        assert result.exit_code == 0
        assert "secret" in result.output.lower() or "classify" in result.output.lower()

    def test_classify_env_file(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".env").write_text(
                "APP_NAME=myapp\nGH_TOKEN=ghp_abc123\nDB_HOST=localhost\nAWS_PROFILE=default\n"
            )
            result = runner.invoke(cli, ["gh", "classify"])
        assert result.exit_code == 0
        assert "1 secrets" in result.output
        assert "2 variables" in result.output
        assert "1 skipped" in result.output

    def test_classify_json_output(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".env").write_text("APP_NAME=myapp\nGH_TOKEN=ghp_abc123\n")
            result = runner.invoke(cli, ["--json", "gh", "classify"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "ok"
        assert len(data["result"]["secrets"]) == 1
        assert data["result"]["secrets"][0]["key"] == "GH_TOKEN"

    def test_classify_missing_file(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(cli, ["gh", "classify", "nonexistent.env"])
        assert result.exit_code != 0

    def test_classify_empty_file(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".env").write_text("")
            result = runner.invoke(cli, ["gh", "classify"])
        assert result.exit_code == 0
        assert "0 secrets" in result.output

    def test_classify_force_var_flag(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".env").write_text("MY_SECRET=some-value\n")
            result = runner.invoke(cli, ["gh", "classify", "--force-var", "MY_SECRET"])
        assert result.exit_code == 0
        assert "0 secrets" in result.output
        assert "1 variables" in result.output

    def test_classify_force_secret_flag(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".env").write_text("APP_NAME=myapp\n")
            result = runner.invoke(cli, ["gh", "classify", "--force-secret", "APP_NAME"])
        assert result.exit_code == 0
        assert "1 secrets" in result.output
        assert "0 variables" in result.output

    def test_classify_json_variables_have_reasons(self):
        """Variables in JSON output now include reasons (e.g., safe value info)."""
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".env").write_text("APP_NAME=myapp\n")
            result = runner.invoke(cli, ["--json", "gh", "classify"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data["result"]["variables"][0], dict)
        assert "key" in data["result"]["variables"][0]


class TestPushCommand:
    def test_push_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["gh", "push", "--help"])
        assert result.exit_code == 0
        assert "--dry-run" in result.output
        assert "--force-var" in result.output
        assert "--force-secret" in result.output

    def test_push_missing_file(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(cli, ["gh", "push", "nonexistent.env"])
        assert result.exit_code != 0
