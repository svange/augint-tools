"""Tests for team_secrets.checkout module."""

from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from augint_tools.team_secrets.checkout import (
    DEFAULT_ORG,
    _commit_and_push,
    _has_changes,
    ephemeral_checkout,
    secrets_repo_slug,
)


def test_secrets_repo_slug_default_org():
    assert secrets_repo_slug("woxom") == "augmenting-integrations/woxom-secrets"


def test_secrets_repo_slug_custom_org():
    assert secrets_repo_slug("woxom", "my-org") == "my-org/woxom-secrets"


def test_default_org():
    assert DEFAULT_ORG == "augmenting-integrations"


class TestHasChanges:
    def test_detects_changes(self):
        with patch(
            "augint_tools.team_secrets.checkout.subprocess.run",
            return_value=CompletedProcess(args=[], returncode=0, stdout=" M file.py\n"),
        ):
            assert _has_changes(Path("/tmp")) is True

    def test_clean_repo(self):
        with patch(
            "augint_tools.team_secrets.checkout.subprocess.run",
            return_value=CompletedProcess(args=[], returncode=0, stdout="   \n"),
        ):
            assert _has_changes(Path("/tmp")) is False


class TestCommitAndPush:
    def test_runs_add_commit_push_in_order(self):
        with patch(
            "augint_tools.team_secrets.checkout.subprocess.run",
            return_value=CompletedProcess(args=[], returncode=0, stdout=""),
        ) as mock_run:
            _commit_and_push(Path("/tmp/repo"))
        invoked = [c.args[0] for c in mock_run.call_args_list]
        assert invoked[0] == ["git", "add", "-A"]
        assert invoked[1][:2] == ["git", "commit"]
        assert "chore: update secrets via ai-tools" in invoked[1][-1]
        assert invoked[2] == ["git", "push"]

    def test_custom_commit_message(self):
        with patch(
            "augint_tools.team_secrets.checkout.subprocess.run",
            return_value=CompletedProcess(args=[], returncode=0, stdout=""),
        ) as mock_run:
            _commit_and_push(Path("/tmp/repo"), message="chore: custom")
        commit_call = mock_run.call_args_list[1].args[0]
        assert commit_call[-1] == "chore: custom"


class TestEphemeralCheckout:
    def test_raises_when_clone_fails(self, tmp_path):
        with (
            patch(
                "augint_tools.team_secrets.checkout.tempfile.mkdtemp",
                return_value=str(tmp_path),
            ),
            patch(
                "augint_tools.team_secrets.checkout.subprocess.run",
                return_value=CompletedProcess(
                    args=[], returncode=1, stdout="", stderr="access denied"
                ),
            ),
            patch("augint_tools.team_secrets.checkout.shutil.rmtree") as mock_rm,
        ):
            with pytest.raises(RuntimeError, match="Failed to clone"):
                with ephemeral_checkout("woxom"):
                    pass
        # Cleanup still happens via finally.
        mock_rm.assert_called_once()

    def test_happy_path_commits_and_pushes(self, tmp_path):
        clone_ok = CompletedProcess(args=[], returncode=0, stdout="")
        with (
            patch(
                "augint_tools.team_secrets.checkout.tempfile.mkdtemp",
                return_value=str(tmp_path),
            ),
            patch(
                "augint_tools.team_secrets.checkout.subprocess.run",
                return_value=clone_ok,
            ),
            patch("augint_tools.team_secrets.checkout._has_changes", return_value=True),
            patch("augint_tools.team_secrets.checkout._commit_and_push") as mock_commit,
            patch("augint_tools.team_secrets.checkout.shutil.rmtree") as mock_rm,
        ):
            with ephemeral_checkout("woxom") as repo_path:
                assert repo_path == tmp_path
        mock_commit.assert_called_once()
        mock_rm.assert_called_once()

    def test_no_changes_skips_commit(self, tmp_path):
        clone_ok = CompletedProcess(args=[], returncode=0, stdout="")
        with (
            patch(
                "augint_tools.team_secrets.checkout.tempfile.mkdtemp",
                return_value=str(tmp_path),
            ),
            patch(
                "augint_tools.team_secrets.checkout.subprocess.run",
                return_value=clone_ok,
            ),
            patch("augint_tools.team_secrets.checkout._has_changes", return_value=False),
            patch("augint_tools.team_secrets.checkout._commit_and_push") as mock_commit,
            patch("augint_tools.team_secrets.checkout.shutil.rmtree"),
        ):
            with ephemeral_checkout("woxom"):
                pass
        mock_commit.assert_not_called()

    def test_push_on_exit_false_skips_commit(self, tmp_path):
        clone_ok = CompletedProcess(args=[], returncode=0, stdout="")
        with (
            patch(
                "augint_tools.team_secrets.checkout.tempfile.mkdtemp",
                return_value=str(tmp_path),
            ),
            patch(
                "augint_tools.team_secrets.checkout.subprocess.run",
                return_value=clone_ok,
            ),
            patch("augint_tools.team_secrets.checkout._has_changes", return_value=True),
            patch("augint_tools.team_secrets.checkout._commit_and_push") as mock_commit,
            patch("augint_tools.team_secrets.checkout.shutil.rmtree"),
        ):
            with ephemeral_checkout("woxom", push_on_exit=False):
                pass
        mock_commit.assert_not_called()
