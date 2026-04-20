"""Tests for chezmoi backup functionality."""

import asyncio
import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from augint_tools.cli.__main__ import cli
from augint_tools.env.chezmoi import build_commit_message, chezmoi_backup


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
        result = runner.invoke(cli, ["sync", "--help"])
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
            result = runner.invoke(cli, ["sync"])
            assert result.exit_code != 0
            assert "not installed" in result.output.lower()

    def test_missing_file(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            with patch(
                "augint_tools.env.chezmoi.shutil.which",
                return_value="/usr/bin/chezmoi",
            ):
                result = runner.invoke(cli, ["sync", "nonexistent.env"])
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
            result = runner.invoke(cli, ["sync"])

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
            result = runner.invoke(cli, ["sync"])

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
            result = runner.invoke(cli, ["sync", "--no-sync"])

        assert result.exit_code == 0
        mock_sync.assert_not_called()

    @patch("augint_tools.env.chezmoi.subprocess.run")
    @patch("augint_tools.env.chezmoi.shutil.which", return_value="/usr/bin/chezmoi")
    def test_dry_run(self, mock_which, mock_run):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".env").write_text("FOO=bar\n")
            result = runner.invoke(cli, ["sync", "--dry-run", "--no-sync"])

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
            result = runner.invoke(cli, ["sync", "--no-sync"])

        assert result.exit_code != 0


class TestConcurrency:
    """Verify that chezmoi and GitHub sync pipelines run in parallel."""

    @patch("augint_tools.env.chezmoi.perform_sync")
    @patch("augint_tools.env.chezmoi.subprocess.run")
    @patch("augint_tools.env.chezmoi.shutil.which", return_value="/usr/bin/chezmoi")
    def test_pipelines_run_concurrently(self, mock_which, mock_run, mock_sync):
        """Both pipelines should execute in parallel, not serially.

        The blocking chezmoi pipeline sleeps 0.2s; the async GitHub sync
        sleeps 0.2s. Sequential execution would take ~0.4s; concurrent
        execution takes ~0.2s. We also assert both pipelines were active
        at the same moment via an Event.
        """
        chezmoi_started = threading.Event()
        github_started = threading.Event()
        overlap_observed = threading.Event()

        def chezmoi_side_effect(cmd, **kwargs):
            # Mark chezmoi entered, then wait briefly for github to start
            # to confirm they overlap.
            chezmoi_started.set()
            if github_started.wait(timeout=1.0):
                overlap_observed.set()
            time.sleep(0.05)
            if cmd == ["chezmoi", "git", "status", "--", "--porcelain"]:
                return subprocess.CompletedProcess(cmd, 0, stdout=" M dot_env\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        async def github_side_effect(filename, dry_run):
            github_started.set()
            # Wait for chezmoi pipeline to also be running.
            await asyncio.sleep(0.2)
            assert chezmoi_started.is_set(), "chezmoi pipeline should have started by now"
            return {"secrets": ["A"], "variables": ["B"]}

        mock_run.side_effect = chezmoi_side_effect
        mock_sync.side_effect = github_side_effect

        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".env").write_text("GH_REPO=r\nGH_ACCOUNT=a\nFOO=bar\n")
            start = time.monotonic()
            result = runner.invoke(cli, ["sync"])
            elapsed = time.monotonic() - start

        assert result.exit_code == 0, result.output
        assert overlap_observed.is_set(), "chezmoi and github pipelines did not overlap"
        # Concurrent runs must be faster than purely sequential. The chezmoi
        # pipeline issues ~6 subprocess calls (~0.3s of sleeps) plus 0.2s for
        # GitHub; serial would be >0.5s. Allow generous slack for CI.
        assert elapsed < 0.45, f"pipelines appear sequential (took {elapsed:.2f}s)"
        mock_sync.assert_called_once()

    @patch("augint_tools.env.chezmoi.perform_sync")
    @patch("augint_tools.env.chezmoi.subprocess.run")
    @patch("augint_tools.env.chezmoi.shutil.which", return_value="/usr/bin/chezmoi")
    def test_both_pipelines_run_even_if_one_fails(self, mock_which, mock_run, mock_sync):
        """If chezmoi fails, GitHub sync still runs (and vice versa)."""
        mock_run.return_value = subprocess.CompletedProcess([], 1, stdout="", stderr="chezmoi boom")

        sync_called = threading.Event()

        async def github_side_effect(filename, dry_run):
            sync_called.set()
            return {"secrets": [], "variables": []}

        mock_sync.side_effect = github_side_effect

        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".env").write_text("GH_REPO=r\nGH_ACCOUNT=a\nFOO=bar\n")
            result = runner.invoke(cli, ["sync"])

        assert result.exit_code != 0
        assert sync_called.is_set(), "GitHub sync should run even when chezmoi fails"
        assert "chezmoi" in result.output.lower()

    @patch("augint_tools.env.chezmoi.perform_sync")
    @patch("augint_tools.env.chezmoi.subprocess.run")
    @patch("augint_tools.env.chezmoi.shutil.which", return_value="/usr/bin/chezmoi")
    def test_aggregates_failures_from_both_pipelines(self, mock_which, mock_run, mock_sync):
        """Both failures are surfaced in the error message."""
        mock_run.return_value = subprocess.CompletedProcess([], 1, stdout="", stderr="chezmoi boom")

        async def github_side_effect(filename, dry_run):
            raise RuntimeError("github boom")

        mock_sync.side_effect = github_side_effect

        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".env").write_text("GH_REPO=r\nGH_ACCOUNT=a\nFOO=bar\n")
            result = runner.invoke(cli, ["sync"])

        assert result.exit_code != 0
        # Both error messages should appear in the surfaced error
        lowered = result.output.lower()
        assert "chezmoi" in lowered
        assert "github" in lowered

    @patch("augint_tools.env.chezmoi.perform_sync")
    @patch("augint_tools.env.chezmoi.subprocess.run")
    @patch("augint_tools.env.chezmoi.shutil.which", return_value="/usr/bin/chezmoi")
    def test_chezmoi_backup_returns_expected_shape_on_success(
        self, mock_which, mock_run, mock_sync
    ):
        """Direct call to chezmoi_backup still returns the expected result dict."""
        mock_sync.return_value = {"secrets": ["S1"], "variables": ["V1", "V2"]}

        def side_effect(cmd, **kwargs):
            if cmd == ["chezmoi", "git", "status", "--", "--porcelain"]:
                return subprocess.CompletedProcess(cmd, 0, stdout=" M dot_env\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        mock_run.side_effect = side_effect

        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".env").write_text("FOO=bar\n")
            result = chezmoi_backup(".env")

        assert result["chezmoi_committed"] is True
        assert result["secrets_synced"] == 1
        assert result["variables_synced"] == 2
