"""Tests for chezmoi backup functionality."""

import subprocess
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from augint_tools.cli.__main__ import cli
from augint_tools.env.chezmoi import build_commit_message


class TestBuildCommitMessage:
    def test_single_file(self):
        status = " M home/user/projects/myrepo/dot_env\n"
        msg = build_commit_message("myrepo", status)
        assert "myrepo" in msg
        assert "dot_env" in msg

    def test_multiple_files(self):
        status = " M dot_env\n A dot_env.local\n"
        msg = build_commit_message("myrepo", status)
        assert "dot_env" in msg
        assert "dot_env.local" in msg

    def test_empty_status(self):
        msg = build_commit_message("myrepo", "")
        assert "myrepo" in msg
        assert "env files" in msg

    def test_commit_message_includes_project_name(self):
        msg = build_commit_message("augint-mono", " M dot_env\n")
        assert "augint-mono" in msg
        assert msg.startswith("chezmoi: sync augint-mono")


class TestChezmoiCliHelp:
    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["env", "chezmoi", "--help"])
        assert result.exit_code == 0
        assert "chezmoi" in result.output.lower()
        assert "--no-sync" in result.output
        assert "--dry-run" in result.output


class TestChezmoiCommand:
    @patch("augint_tools.env.chezmoi.shutil.which", return_value=None)
    def test_not_installed(self, mock_which):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".env").write_text("FOO=bar\n")
            result = runner.invoke(cli, ["env", "chezmoi"])
            assert result.exit_code != 0
            assert "not installed" in result.output.lower()

    def test_missing_file(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            with patch(
                "augint_tools.env.chezmoi.shutil.which",
                return_value="/usr/bin/chezmoi",
            ):
                result = runner.invoke(cli, ["env", "chezmoi", "nonexistent.env"])
                assert result.exit_code != 0

    @patch("augint_tools.env.chezmoi.perform_sync")
    @patch("augint_tools.env.chezmoi.subprocess.run")
    @patch("augint_tools.env.chezmoi.shutil.which", return_value="/usr/bin/chezmoi")
    def test_full_flow(self, mock_which, mock_run, mock_sync):
        mock_sync.return_value = {"secrets": ["GH_TOKEN"], "variables": ["APP_NAME"]}

        def side_effect(cmd, **kwargs):
            if cmd == ["chezmoi", "git", "status", "--", "--porcelain"]:
                return subprocess.CompletedProcess(cmd, 0, stdout=" M dot_env\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        mock_run.side_effect = side_effect

        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".env").write_text("GH_REPO=test\nGH_ACCOUNT=acct\nGH_TOKEN=tok\n")
            result = runner.invoke(cli, ["env", "chezmoi"])

        assert result.exit_code == 0, result.output

        cmds = [c.args[0] for c in mock_run.call_args_list]
        assert cmds[0][1] == "add"
        assert cmds[1][1:3] == ["git", "add"]
        assert cmds[2][1:3] == ["git", "status"]
        assert cmds[3][1:3] == ["git", "commit"]
        assert cmds[4][1:3] == ["git", "pull"]
        assert "--rebase" in cmds[4]
        assert cmds[5][1:3] == ["git", "push"]

        mock_sync.assert_called_once()

    @patch("augint_tools.env.chezmoi.perform_sync")
    @patch("augint_tools.env.chezmoi.subprocess.run")
    @patch("augint_tools.env.chezmoi.shutil.which", return_value="/usr/bin/chezmoi")
    def test_no_changes_skips_commit(self, mock_which, mock_run, mock_sync):
        mock_sync.return_value = {"secrets": [], "variables": []}
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".env").write_text("FOO=bar\n")
            result = runner.invoke(cli, ["env", "chezmoi"])

        assert result.exit_code == 0
        assert mock_run.call_count == 3

    @patch("augint_tools.env.chezmoi.perform_sync")
    @patch("augint_tools.env.chezmoi.subprocess.run")
    @patch("augint_tools.env.chezmoi.shutil.which", return_value="/usr/bin/chezmoi")
    def test_no_sync_flag(self, mock_which, mock_run, mock_sync):
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".env").write_text("FOO=bar\n")
            result = runner.invoke(cli, ["env", "chezmoi", "--no-sync"])

        assert result.exit_code == 0
        mock_sync.assert_not_called()

    @patch("augint_tools.env.chezmoi.subprocess.run")
    @patch("augint_tools.env.chezmoi.shutil.which", return_value="/usr/bin/chezmoi")
    def test_dry_run(self, mock_which, mock_run):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".env").write_text("FOO=bar\n")
            result = runner.invoke(cli, ["env", "chezmoi", "--dry-run", "--no-sync"])

        assert result.exit_code == 0
        assert "DRY RUN" in result.output
        mock_run.assert_not_called()

    @patch("augint_tools.env.chezmoi.subprocess.run")
    @patch("augint_tools.env.chezmoi.shutil.which", return_value="/usr/bin/chezmoi")
    def test_chezmoi_failure(self, mock_which, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            [], 1, stdout="", stderr="permission denied"
        )

        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".env").write_text("FOO=bar\n")
            result = runner.invoke(cli, ["env", "chezmoi", "--no-sync"])

        assert result.exit_code != 0
