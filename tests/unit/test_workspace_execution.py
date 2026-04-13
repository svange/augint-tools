"""Tests for workspace execution utilities."""

from pathlib import Path
from unittest.mock import patch

from augint_tools.config import RepoConfig
from augint_tools.execution.workspace import get_repo_path, resolve_clone_url


def _make_repo_config(**kwargs: object) -> RepoConfig:
    defaults: dict[str, object] = {
        "name": "my-lib",
        "path": "repos/my-lib",
        "url": "https://github.com/myorg/my-lib.git",
        "repo_type": "library",
        "base_branch": "main",
        "pr_target_branch": "main",
    }
    defaults.update(kwargs)
    return RepoConfig(**defaults)  # type: ignore[arg-type]


class TestGetRepoPath:
    def test_relative_path_default(self, tmp_path: Path) -> None:
        """Configured relative path used when no .git anywhere."""
        rc = _make_repo_config(path="repos/my-lib")
        result = get_repo_path(tmp_path, rc)
        assert result == tmp_path / "repos" / "my-lib"

    def test_absolute_path(self, tmp_path: Path) -> None:
        """Absolute path in config returned as-is."""
        rc = _make_repo_config(path="/absolute/my-lib")
        result = get_repo_path(tmp_path, rc)
        assert result == Path("/absolute/my-lib")

    def test_configured_path_with_git(self, tmp_path: Path) -> None:
        """Configured path wins when it has a .git dir."""
        configured = tmp_path / "repos" / "my-lib"
        (configured / ".git").mkdir(parents=True)
        rc = _make_repo_config(path="repos/my-lib")
        result = get_repo_path(tmp_path, rc)
        assert result == configured

    def test_sibling_fallback(self, tmp_path: Path) -> None:
        """Sibling path used when configured path has no .git but sibling does."""
        workspace_root = tmp_path / "workspace-repo"
        workspace_root.mkdir()
        # configured path exists but has no .git
        (workspace_root / "repos" / "my-lib").mkdir(parents=True)
        # sibling has .git
        sibling = tmp_path / "my-lib"
        (sibling / ".git").mkdir(parents=True)

        rc = _make_repo_config(path="repos/my-lib")
        result = get_repo_path(workspace_root, rc)
        assert result == sibling

    def test_configured_path_wins_over_sibling(self, tmp_path: Path) -> None:
        """When both configured and sibling have .git, configured wins."""
        workspace_root = tmp_path / "workspace-repo"
        workspace_root.mkdir()
        configured = workspace_root / "repos" / "my-lib"
        (configured / ".git").mkdir(parents=True)
        sibling = tmp_path / "my-lib"
        (sibling / ".git").mkdir(parents=True)

        rc = _make_repo_config(path="repos/my-lib")
        result = get_repo_path(workspace_root, rc)
        assert result == configured

    def test_env_var_override_absolute(self, tmp_path: Path, monkeypatch: object) -> None:
        """WORKSPACE_REPOS_DIR env var with absolute path takes full precedence."""
        repos_dir = tmp_path / "override"
        repos_dir.mkdir()
        import pytest

        mp = pytest.MonkeyPatch()
        mp.setenv("WORKSPACE_REPOS_DIR", str(repos_dir))
        try:
            rc = _make_repo_config(name="my-lib", path="repos/my-lib")
            result = get_repo_path(tmp_path, rc)
            assert result == repos_dir / "my-lib"
        finally:
            mp.undo()

    def test_env_var_override_relative(self, tmp_path: Path) -> None:
        """Relative WORKSPACE_REPOS_DIR resolved against base_path."""
        import pytest

        mp = pytest.MonkeyPatch()
        mp.setenv("WORKSPACE_REPOS_DIR", "custom-repos")
        try:
            rc = _make_repo_config(name="my-lib")
            result = get_repo_path(tmp_path, rc)
            assert result == tmp_path / "custom-repos" / "my-lib"
        finally:
            mp.undo()

    def test_no_sibling_fallback_when_no_git(self, tmp_path: Path) -> None:
        """Falls through to configured path when neither has .git."""
        workspace_root = tmp_path / "workspace-repo"
        workspace_root.mkdir()
        # sibling exists but no .git
        (tmp_path / "my-lib").mkdir()

        rc = _make_repo_config(path="repos/my-lib")
        result = get_repo_path(workspace_root, rc)
        assert result == workspace_root / "repos" / "my-lib"


class TestResolveCloneUrl:
    def test_default_url(self) -> None:
        """Without env var or proxy, returns repo_config.url."""
        rc = _make_repo_config(url="https://github.com/myorg/my-lib.git")
        with patch("augint_tools.execution.workspace.get_remote_url", return_value=None):
            result = resolve_clone_url(rc, Path("/workspace"))
        assert result == "https://github.com/myorg/my-lib.git"

    def test_template_env_var(self) -> None:
        """GIT_CLONE_URL_TEMPLATE substitutes org and repo."""
        import pytest

        mp = pytest.MonkeyPatch()
        mp.setenv(
            "GIT_CLONE_URL_TEMPLATE",
            "http://local_proxy@127.0.0.1:9999/git/{org}/{repo}",
        )
        try:
            rc = _make_repo_config(url="https://github.com/myorg/my-lib.git")
            result = resolve_clone_url(rc, Path("/workspace"))
            assert result == "http://local_proxy@127.0.0.1:9999/git/myorg/my-lib"
        finally:
            mp.undo()

    def test_template_with_slug_placeholder(self) -> None:
        """GIT_CLONE_URL_TEMPLATE with {slug} placeholder."""
        import pytest

        mp = pytest.MonkeyPatch()
        mp.setenv(
            "GIT_CLONE_URL_TEMPLATE",
            "https://mirror.example.com/{slug}.git",
        )
        try:
            rc = _make_repo_config(url="https://github.com/myorg/my-lib.git")
            result = resolve_clone_url(rc, Path("/workspace"))
            assert result == "https://mirror.example.com/myorg/my-lib.git"
        finally:
            mp.undo()

    def test_proxy_detection_from_origin(self) -> None:
        """Detects proxy URL from workspace origin and rewrites clone URL."""
        rc = _make_repo_config(url="https://github.com/myorg/my-lib.git")
        proxy_origin = "http://local_proxy@127.0.0.1:8080/git/myorg/workspace-repo"
        with patch("augint_tools.execution.workspace.get_remote_url", return_value=proxy_origin):
            result = resolve_clone_url(rc, Path("/workspace"))
        assert result == "http://local_proxy@127.0.0.1:8080/git/myorg/my-lib"

    def test_no_proxy_for_normal_origin(self) -> None:
        """Normal origin URL does not trigger proxy rewriting."""
        rc = _make_repo_config(url="https://github.com/myorg/my-lib.git")
        normal_origin = "https://github.com/myorg/workspace-repo.git"
        with patch("augint_tools.execution.workspace.get_remote_url", return_value=normal_origin):
            result = resolve_clone_url(rc, Path("/workspace"))
        assert result == "https://github.com/myorg/my-lib.git"

    def test_ssh_url_with_proxy_origin(self) -> None:
        """SSH URLs have their slug extracted for proxy rewriting."""
        rc = _make_repo_config(url="git@github.com:myorg/my-lib.git")
        proxy_origin = "http://local_proxy@127.0.0.1:8080/git/myorg/workspace-repo"
        with patch("augint_tools.execution.workspace.get_remote_url", return_value=proxy_origin):
            result = resolve_clone_url(rc, Path("/workspace"))
        assert result == "http://local_proxy@127.0.0.1:8080/git/myorg/my-lib"

    def test_env_var_takes_precedence_over_proxy(self) -> None:
        """GIT_CLONE_URL_TEMPLATE wins over proxy detection."""
        import pytest

        mp = pytest.MonkeyPatch()
        mp.setenv(
            "GIT_CLONE_URL_TEMPLATE",
            "https://mirror.example.com/{slug}.git",
        )
        try:
            rc = _make_repo_config(url="https://github.com/myorg/my-lib.git")
            proxy_origin = "http://local_proxy@127.0.0.1:8080/git/myorg/workspace-repo"
            with patch(
                "augint_tools.execution.workspace.get_remote_url",
                return_value=proxy_origin,
            ):
                result = resolve_clone_url(rc, Path("/workspace"))
            assert result == "https://mirror.example.com/myorg/my-lib.git"
        finally:
            mp.undo()

    def test_unparseable_url_falls_through(self) -> None:
        """URL that can't be parsed returns as-is."""
        rc = _make_repo_config(url="https://gitlab.internal/project/my-lib.git")
        proxy_origin = "http://local_proxy@127.0.0.1:8080/git/myorg/workspace-repo"
        with patch("augint_tools.execution.workspace.get_remote_url", return_value=proxy_origin):
            result = resolve_clone_url(rc, Path("/workspace"))
        assert result == "https://gitlab.internal/project/my-lib.git"
