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

    def test_repo_status_stub(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["repo", "status"])
        assert result.exit_code == 0
        assert '"command": "status"' in result.output
        assert '"implemented": false' in result.output
        assert '"scope": "repo"' in result.output

    def test_monorepo_status_stub(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["monorepo", "status"])
        assert result.exit_code == 0
        assert '"command": "status"' in result.output
        assert '"implemented": false' in result.output
        assert '"scope": "monorepo"' in result.output

    def test_mono_alias(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["mono", "--help"])
        assert result.exit_code == 0
        assert "Workspace and monorepo orchestration commands" in result.output
