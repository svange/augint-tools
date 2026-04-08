"""Tests for git module."""

from unittest.mock import Mock, patch

from augint_tools.git import (
    detect_base_branch,
    get_ahead_behind,
    get_current_branch,
    get_dirty_files,
    is_git_repo,
)


class TestGitRepo:
    @patch("augint_tools.git.repo.run_git")
    def test_is_git_repo_true(self, mock_run_git):
        """Test is_git_repo when in a git repo."""
        mock_run_git.return_value = Mock(returncode=0, stdout=".git")

        assert is_git_repo() is True
        mock_run_git.assert_called_once()

    @patch("augint_tools.git.repo.run_git")
    def test_is_git_repo_false(self, mock_run_git):
        """Test is_git_repo when not in a git repo."""
        mock_run_git.return_value = Mock(returncode=128, stdout="")

        assert is_git_repo() is False

    @patch("augint_tools.git.repo.run_git")
    def test_get_current_branch(self, mock_run_git):
        """Test getting current branch."""
        mock_run_git.return_value = Mock(returncode=0, stdout="feat/test\n")

        branch = get_current_branch()
        assert branch == "feat/test"

    @patch("augint_tools.git.repo.run_git")
    def test_get_current_branch_detached(self, mock_run_git):
        """Test getting branch in detached HEAD."""
        mock_run_git.return_value = Mock(returncode=0, stdout="")

        branch = get_current_branch()
        assert branch is None

    @patch("augint_tools.git.repo.run_git")
    def test_detect_base_branch_main(self, mock_run_git):
        """Test detecting main as base branch."""
        mock_run_git.return_value = Mock(returncode=0, stdout="* main\n  dev\n  feat/test\n")

        branch = detect_base_branch()
        assert branch == "main"

    @patch("augint_tools.git.repo.run_git")
    def test_detect_base_branch_dev(self, mock_run_git):
        """Test detecting dev as base branch."""
        mock_run_git.return_value = Mock(returncode=0, stdout="* dev\n  feat/test\n")

        branch = detect_base_branch()
        assert branch == "dev"


class TestGitStatus:
    @patch("augint_tools.git.status.run_git")
    def test_get_dirty_files(self, mock_run_git):
        """Test getting dirty files."""
        mock_run_git.return_value = Mock(returncode=0, stdout=" M file1.py\n?? file2.py\n")

        files = get_dirty_files()
        assert len(files) == 2
        assert "file1.py" in files
        assert "file2.py" in files

    @patch("augint_tools.git.status.run_git")
    def test_get_dirty_files_clean(self, mock_run_git):
        """Test getting dirty files when clean."""
        mock_run_git.return_value = Mock(returncode=0, stdout="")

        files = get_dirty_files()
        assert len(files) == 0

    @patch("augint_tools.git.status.run_git")
    def test_get_ahead_behind(self, mock_run_git):
        """Test getting ahead/behind counts."""
        # Mock two calls: upstream branch lookup, then ahead/behind
        mock_run_git.side_effect = [
            Mock(returncode=0, stdout="origin/main\n"),
            Mock(returncode=0, stdout="3\t2\n"),
        ]

        ahead, behind = get_ahead_behind()
        assert ahead == 3
        assert behind == 2

    @patch("augint_tools.git.status.run_git")
    def test_get_ahead_behind_no_upstream(self, mock_run_git):
        """Test getting ahead/behind when no upstream."""
        mock_run_git.return_value = Mock(returncode=128, stdout="")

        ahead, behind = get_ahead_behind()
        assert ahead == 0
        assert behind == 0
