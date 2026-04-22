"""Tests for git module."""

from unittest.mock import Mock, patch

from augint_tools.git import (
    branch_exists,
    create_branch,
    detect_base_branch,
    extract_repo_slug,
    get_ahead_behind,
    get_current_branch,
    get_dirty_files,
    get_remote_url,
    get_repo_status,
    is_git_repo,
    push_branch,
    run_git,
    switch_branch,
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


class TestExtractRepoSlug:
    def test_https_url(self):
        assert extract_repo_slug("https://github.com/myorg/my-repo.git") == "myorg/my-repo"

    def test_https_url_no_suffix(self):
        assert extract_repo_slug("https://github.com/myorg/my-repo") == "myorg/my-repo"

    def test_ssh_url(self):
        assert extract_repo_slug("git@github.com:myorg/my-repo.git") == "myorg/my-repo"

    def test_proxy_url(self):
        assert (
            extract_repo_slug("http://local_proxy@127.0.0.1:8080/git/myorg/my-repo")
            == "myorg/my-repo"
        )

    def test_proxy_url_https(self):
        assert extract_repo_slug("https://local_proxy@127.0.0.1:9999/git/Org/Repo") == "Org/Repo"

    def test_non_github_url(self):
        assert extract_repo_slug("https://gitlab.com/myorg/my-repo.git") is None

    def test_trailing_slash(self):
        assert extract_repo_slug("https://github.com/myorg/my-repo/") == "myorg/my-repo"


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

    @patch("augint_tools.git.status.run_git")
    def test_get_ahead_behind_explicit_remote(self, mock_run_git):
        mock_run_git.return_value = Mock(returncode=0, stdout="1\t4\n")
        ahead, behind = get_ahead_behind(remote_branch="origin/dev")
        assert (ahead, behind) == (1, 4)
        # No upstream lookup when explicit remote provided.
        assert mock_run_git.call_count == 1

    @patch("augint_tools.git.status.run_git")
    def test_get_ahead_behind_malformed_output(self, mock_run_git):
        # Single field instead of two -> defaults to (0, 0) without blowing up.
        mock_run_git.return_value = Mock(returncode=0, stdout="oops\n")
        assert get_ahead_behind(remote_branch="origin/main") == (0, 0)

    @patch("augint_tools.git.status.run_git")
    def test_get_ahead_behind_compare_fails(self, mock_run_git):
        mock_run_git.side_effect = [
            Mock(returncode=0, stdout="origin/main\n"),
            Mock(returncode=128, stdout=""),
        ]
        assert get_ahead_behind() == (0, 0)

    @patch("augint_tools.git.status.run_git")
    def test_get_dirty_files_error(self, mock_run_git):
        mock_run_git.return_value = Mock(returncode=128, stdout="")
        assert get_dirty_files() == []

    @patch("augint_tools.git.status.get_dirty_files")
    @patch("augint_tools.git.status.get_ahead_behind")
    @patch("augint_tools.git.repo.get_current_branch")
    def test_get_repo_status_detects_branch(self, mock_branch, mock_ahead, mock_dirty):
        mock_branch.return_value = "feat/x"
        mock_ahead.return_value = (2, 1)
        mock_dirty.return_value = ["a.py", "b.py"]
        status = get_repo_status()
        assert status.branch == "feat/x"
        assert status.dirty is True
        assert status.dirty_files == ["a.py", "b.py"]
        assert (status.ahead, status.behind) == (2, 1)

    @patch("augint_tools.git.status.get_dirty_files")
    @patch("augint_tools.git.status.get_ahead_behind")
    def test_get_repo_status_clean_with_explicit_branch(self, mock_ahead, mock_dirty):
        mock_ahead.return_value = (0, 0)
        mock_dirty.return_value = []
        status = get_repo_status(branch="main")
        assert status.branch == "main"
        assert status.dirty is False
        assert status.dirty_files == []


class TestGitRepoExtras:
    @patch("augint_tools.git.repo.subprocess.run")
    def test_run_git_invokes_subprocess(self, mock_run):
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
        run_git(["status"], check=False)
        args, kwargs = mock_run.call_args
        assert args[0][0] == "git"
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["check"] is False

    @patch("augint_tools.git.repo.run_git")
    def test_get_remote_url_found(self, mock_run_git):
        mock_run_git.return_value = Mock(returncode=0, stdout="https://github.com/org/repo.git\n")
        assert get_remote_url() == "https://github.com/org/repo.git"

    @patch("augint_tools.git.repo.run_git")
    def test_get_remote_url_missing(self, mock_run_git):
        mock_run_git.return_value = Mock(returncode=2, stdout="")
        assert get_remote_url(remote="upstream") is None

    @patch("augint_tools.git.repo.run_git")
    def test_detect_base_branch_remote_only(self, mock_run_git):
        # No local candidate, but remote has main -> returns main.
        mock_run_git.return_value = Mock(returncode=0, stdout="* feat/x\n  remotes/origin/main\n")
        assert detect_base_branch() == "main"

    @patch("augint_tools.git.repo.run_git")
    def test_detect_base_branch_falls_back_to_main(self, mock_run_git):
        # No candidate anywhere.
        mock_run_git.return_value = Mock(returncode=0, stdout="* feat/x\n")
        assert detect_base_branch() == "main"

    @patch("augint_tools.git.repo.run_git")
    def test_detect_base_branch_error_defaults_main(self, mock_run_git):
        mock_run_git.return_value = Mock(returncode=128, stdout="")
        assert detect_base_branch() == "main"

    def test_extract_repo_slug_proxy_without_enough_parts(self):
        # Proxy path missing the repo segment returns None instead of a partial.
        assert extract_repo_slug("http://local_proxy@127.0.0.1:8080/git/onlyowner") is None

    def test_extract_repo_slug_ssh_malformed(self):
        # Two colons means we can't cleanly split into "host:slug".
        assert extract_repo_slug("git@github.com:a:b") is None


class TestBranchOps:
    @patch("augint_tools.git.branch.run_git")
    def test_create_branch_success(self, mock_run_git):
        mock_run_git.return_value = Mock(returncode=0)
        assert create_branch(name="feat/x", base="main") is True
        args = mock_run_git.call_args[0][0]
        assert args == ["checkout", "-b", "feat/x", "main"]

    @patch("augint_tools.git.branch.run_git")
    def test_create_branch_failure(self, mock_run_git):
        mock_run_git.return_value = Mock(returncode=1)
        assert create_branch(name="feat/x") is False

    @patch("augint_tools.git.branch.run_git")
    def test_create_branch_exception(self, mock_run_git):
        mock_run_git.side_effect = RuntimeError("boom")
        assert create_branch(name="feat/x") is False

    @patch("augint_tools.git.branch.run_git")
    def test_switch_branch_success(self, mock_run_git):
        mock_run_git.return_value = Mock(returncode=0)
        assert switch_branch(name="main") is True
        assert mock_run_git.call_args[0][0] == ["checkout", "main"]

    @patch("augint_tools.git.branch.run_git")
    def test_switch_branch_failure(self, mock_run_git):
        mock_run_git.return_value = Mock(returncode=1)
        assert switch_branch(name="missing") is False

    @patch("augint_tools.git.branch.run_git")
    def test_switch_branch_exception(self, mock_run_git):
        mock_run_git.side_effect = RuntimeError("boom")
        assert switch_branch(name="main") is False

    @patch("augint_tools.git.branch.run_git")
    def test_push_branch_with_upstream(self, mock_run_git):
        mock_run_git.return_value = Mock(returncode=0)
        assert push_branch(branch="feat/x") is True
        assert mock_run_git.call_args[0][0] == ["push", "-u", "origin", "feat/x"]

    @patch("augint_tools.git.branch.run_git")
    def test_push_branch_without_upstream(self, mock_run_git):
        mock_run_git.return_value = Mock(returncode=0)
        assert push_branch(branch="feat/x", set_upstream=False) is True
        assert mock_run_git.call_args[0][0] == ["push", "origin", "feat/x"]

    @patch("augint_tools.git.branch.run_git")
    def test_push_branch_without_upstream_no_branch(self, mock_run_git):
        mock_run_git.return_value = Mock(returncode=0)
        assert push_branch(set_upstream=False) is True
        assert mock_run_git.call_args[0][0] == ["push", "origin"]

    @patch("augint_tools.git.branch.run_git")
    def test_push_branch_failure(self, mock_run_git):
        mock_run_git.return_value = Mock(returncode=1)
        assert push_branch(branch="feat/x") is False

    @patch("augint_tools.git.branch.run_git")
    def test_push_branch_exception(self, mock_run_git):
        mock_run_git.side_effect = RuntimeError("boom")
        assert push_branch(branch="feat/x") is False

    @patch("augint_tools.git.branch.run_git")
    def test_branch_exists_true(self, mock_run_git):
        mock_run_git.return_value = Mock(returncode=0)
        assert branch_exists(name="main") is True

    @patch("augint_tools.git.branch.run_git")
    def test_branch_exists_false(self, mock_run_git):
        mock_run_git.return_value = Mock(returncode=128)
        assert branch_exists(name="nope") is False

    @patch("augint_tools.git.branch.run_git")
    def test_branch_exists_exception(self, mock_run_git):
        mock_run_git.side_effect = RuntimeError("boom")
        assert branch_exists(name="x") is False


class TestIsGitRepoException:
    @patch("augint_tools.git.repo.run_git")
    def test_is_git_repo_exception_swallowed(self, mock_run_git):
        mock_run_git.side_effect = RuntimeError("boom")
        assert is_git_repo() is False

    @patch("augint_tools.git.repo.run_git")
    def test_get_current_branch_exception(self, mock_run_git):
        mock_run_git.side_effect = RuntimeError("boom")
        assert get_current_branch() is None
