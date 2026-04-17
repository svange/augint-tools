"""Tests for CLI module."""

from click.testing import CliRunner

from augint_tools.cli.__main__ import cli
from augint_tools.cli.commands import init as init_cmd


class TestCli:
    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "CLI for AI-assisted repository workflows." in result.output
        assert "gh" in result.output
        assert "init" in result.output
        assert "config" in result.output

    def test_init_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["init", "--help"])
        assert result.exit_code == 0
        assert "scaffold wizard" in result.output.lower()

    def test_config_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["config", "--help"])
        assert result.exit_code == 0
        assert "Configure IDE settings" in result.output

    def test_global_flags_in_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert "--json" in result.output
        assert "--verbose" in result.output


class TestInitPrereqCheck:
    """Regression: missing prerequisites must exit early and print install hints."""

    def test_missing_prereq_shows_install_hint(self, monkeypatch):
        monkeypatch.setattr(init_cmd.shutil, "which", lambda _tool: None)
        runner = CliRunner()
        # Select npm library (option 2) -- requires npm which we force-missing.
        result = runner.invoke(cli, ["init"], input="2\n")
        assert result.exit_code == 1
        assert "Missing required tool(s)" in result.output
        assert "npm" in result.output
        # Platform-specific command or docs URL should appear.
        assert "Docs:" in result.output

    def test_every_prereq_has_install_hint(self):
        all_prereqs = {p for pt in init_cmd.PROJECT_TYPES for p in pt.prereqs}
        missing_hints = all_prereqs - set(init_cmd._INSTALL_HINTS.keys())
        assert not missing_hints, f"Missing install hints for: {missing_hints}"
