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
        assert "monorepo" in result.output

    def test_repo_status(self):
        """Test repo status in a real git repo."""
        runner = CliRunner()
        result = runner.invoke(cli, ["repo", "status"])
        # Should succeed (we're in a git repo)
        assert result.exit_code == 0
        assert "status" in result.output.lower()
        assert "branch" in result.output.lower()

    def test_repo_status_json(self):
        """Test repo status with JSON output."""
        runner = CliRunner()
        result = runner.invoke(cli, ["repo", "status", "--json"])
        assert result.exit_code == 0

        # Parse JSON
        data = json.loads(result.output)
        assert data["command"] == "status"
        assert data["scope"] == "repo"
        assert "repo" in data
        assert "branch" in data["repo"]

    def test_monorepo_status_no_workspace(self):
        """Test monorepo status without workspace.toml."""
        runner = CliRunner()
        result = runner.invoke(cli, ["monorepo", "status"])
        # Should fail (no workspace.toml)
        assert result.exit_code == 1
        assert "workspace.toml" in result.output.lower()

    def test_monorepo_status_json_no_workspace(self):
        """Test monorepo status JSON without workspace.toml."""
        runner = CliRunner()
        result = runner.invoke(cli, ["monorepo", "status", "--json"])
        # Should fail (no workspace.toml)
        assert result.exit_code == 0  # JSON mode doesn't exit with error

        # Parse JSON
        data = json.loads(result.output)
        assert data["command"] == "status"
        assert data["scope"] == "monorepo"
        assert data["status"] == "error"
        assert "workspace.toml" in data["error"].lower()
