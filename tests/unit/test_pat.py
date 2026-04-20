"""Tests for augint_tools.pat core logic."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from augint_tools.cli.__main__ import cli
from augint_tools.pat import (
    PatCreationError,
    PatCredentials,
    PatRequest,
    create_pat,
    parse_permissions,
    parse_repo_specs,
    resolve_credentials,
    write_token_to_env,
)


class TestParsePermissions:
    def test_single_permission(self):
        result = parse_permissions("contents=write")
        assert len(result) == 1
        key, value = next(iter(result.items()))
        assert value.name == "WRITE"

    def test_multiple_permissions(self):
        result = parse_permissions("contents=write,metadata=read")
        assert len(result) == 2
        values = {v.name for v in result.values()}
        assert values == {"READ", "WRITE"}

    def test_spaces_tolerated(self):
        result = parse_permissions("  contents = write , metadata = read  ")
        assert len(result) == 2

    def test_none_level(self):
        result = parse_permissions("gists=none")
        levels = [v.name for v in result.values()]
        assert levels == ["NONE"]

    def test_case_insensitive_level(self):
        parse_permissions("contents=WRITE")
        parse_permissions("contents=Write")

    def test_unknown_key_raises(self):
        with pytest.raises(ValueError, match="unknown permission key"):
            parse_permissions("not_a_real_permission=read")

    def test_unknown_level_raises(self):
        with pytest.raises(ValueError, match="invalid permission level"):
            parse_permissions("contents=admin")

    def test_missing_equals_raises(self):
        with pytest.raises(ValueError, match="expected key=level"):
            parse_permissions("contents")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="empty"):
            parse_permissions("")

    def test_only_whitespace_raises(self):
        with pytest.raises(ValueError, match="empty"):
            parse_permissions("   ")


class TestParseRepoSpecs:
    def test_single_repo(self):
        owner, names = parse_repo_specs(["svange/augint-tools"])
        assert owner == "svange"
        assert names == ["augint-tools"]

    def test_multiple_repos_same_owner(self):
        owner, names = parse_repo_specs(["svange/a", "svange/b", "svange/c"])
        assert owner == "svange"
        assert names == ["a", "b", "c"]

    def test_differing_owners_raise(self):
        with pytest.raises(ValueError, match="same owner"):
            parse_repo_specs(["svange/a", "other/b"])

    def test_missing_slash_raises(self):
        with pytest.raises(ValueError, match="owner/repo"):
            parse_repo_specs(["just-a-name"])

    def test_empty_owner_raises(self):
        with pytest.raises(ValueError, match="owner/repo"):
            parse_repo_specs(["/repo"])

    def test_empty_name_raises(self):
        with pytest.raises(ValueError, match="owner/repo"):
            parse_repo_specs(["owner/"])

    def test_empty_list_raises(self):
        with pytest.raises(ValueError, match="at least one"):
            parse_repo_specs([])


class TestResolveCredentials:
    def test_uses_env_vars(self, monkeypatch):
        monkeypatch.setenv("GITHUB_USERNAME", "alice")
        monkeypatch.setenv("GITHUB_PASSWORD", "s3cr3t")
        creds = resolve_credentials(interactive=False)
        assert creds.username == "alice"
        assert creds.password == "s3cr3t"

    def test_missing_username_non_interactive_raises(self, monkeypatch):
        monkeypatch.delenv("GITHUB_USERNAME", raising=False)
        monkeypatch.setenv("GITHUB_PASSWORD", "pw")
        with pytest.raises(PatCreationError, match="GITHUB_USERNAME"):
            resolve_credentials(interactive=False)

    def test_missing_password_non_interactive_raises(self, monkeypatch):
        monkeypatch.setenv("GITHUB_USERNAME", "alice")
        monkeypatch.delenv("GITHUB_PASSWORD", raising=False)
        with pytest.raises(PatCreationError, match="GITHUB_PASSWORD"):
            resolve_credentials(interactive=False)

    def test_custom_env_var_names(self, monkeypatch):
        monkeypatch.setenv("MY_USER", "bob")
        monkeypatch.setenv("MY_PASS", "hunter2")
        creds = resolve_credentials(
            username_env="MY_USER", password_env="MY_PASS", interactive=False
        )
        assert creds.username == "bob"


class TestCreatePat:
    def _build_request(self) -> PatRequest:
        perms = parse_permissions("contents=write")
        return PatRequest(
            name="t",
            owner="svange",
            repo_names=["augint-tools"],
            permissions=perms,
        )

    def _run(self, coro):
        return asyncio.run(coro)

    def test_returns_token_on_success(self):
        request = self._build_request()
        creds = PatCredentials(username="u", password="p")

        session = MagicMock()
        session.create_token = AsyncMock(return_value="ghp_fake_token_abc")

        with patch("augint_tools.pat.async_client", asyncio_context(session)):
            token = self._run(create_pat(request, creds))
        assert token == "ghp_fake_token_abc"
        session.create_token.assert_awaited_once()

    def test_login_error_wrapped(self):
        from github_fine_grained_token_client import LoginError

        request = self._build_request()
        creds = PatCredentials(username="u", password="p")

        session = MagicMock()
        session.create_token = AsyncMock(side_effect=LoginError("bad creds"))

        with patch("augint_tools.pat.async_client", asyncio_context(session)):
            with pytest.raises(PatCreationError, match="authentication failed"):
                self._run(create_pat(request, creds))

    def test_token_name_taken_wrapped(self):
        from github_fine_grained_token_client import TokenNameAlreadyTakenError

        request = self._build_request()
        creds = PatCredentials(username="u", password="p")

        session = MagicMock()
        session.create_token = AsyncMock(side_effect=TokenNameAlreadyTakenError("taken"))

        with patch("augint_tools.pat.async_client", asyncio_context(session)):
            with pytest.raises(PatCreationError, match="already taken"):
                self._run(create_pat(request, creds))

    def test_too_many_attempts_wrapped(self):
        from github_fine_grained_token_client import TooManyAttemptsError

        request = self._build_request()
        creds = PatCredentials(username="u", password="p")

        session = MagicMock()
        session.create_token = AsyncMock(side_effect=TooManyAttemptsError("slow down"))

        with patch("augint_tools.pat.async_client", asyncio_context(session)):
            with pytest.raises(PatCreationError, match="rate-limited"):
                self._run(create_pat(request, creds))


class TestWriteTokenToEnv:
    def test_creates_new_file(self, tmp_path):
        env = tmp_path / "nested" / ".env"
        write_token_to_env(env, "GH_TOKEN", "ghp_x")
        assert env.exists()
        assert "GH_TOKEN" in env.read_text()
        assert "ghp_x" in env.read_text()

    def test_updates_existing_var(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("GH_TOKEN=old_value\nOTHER=keep\n")
        write_token_to_env(env, "GH_TOKEN", "new_value")
        content = env.read_text()
        assert "new_value" in content
        assert "old_value" not in content
        assert "OTHER=keep" in content

    def test_appends_new_var(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("EXISTING=value\n")
        write_token_to_env(env, "GH_TOKEN", "ghp_x")
        content = env.read_text()
        assert "EXISTING=value" in content
        assert "GH_TOKEN" in content


class TestPatCli:
    def test_pat_in_top_level_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "pat" in result.output

    def test_pat_create_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["pat", "create", "--help"])
        assert result.exit_code == 0
        assert "--repo" in result.output
        assert "--permissions" in result.output
        assert "--env-file" in result.output

    def test_invalid_repo_spec_errors_before_network(self, monkeypatch):
        monkeypatch.setenv("GITHUB_USERNAME", "u")
        monkeypatch.setenv("GITHUB_PASSWORD", "p")
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "pat",
                "create",
                "--repo",
                "not-a-valid-spec",
                "--name",
                "t",
                "--permissions",
                "contents=write",
            ],
        )
        assert result.exit_code != 0
        assert "owner/repo" in result.output

    def test_mixed_owners_errors_before_network(self, monkeypatch):
        monkeypatch.setenv("GITHUB_USERNAME", "u")
        monkeypatch.setenv("GITHUB_PASSWORD", "p")
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "pat",
                "create",
                "--repo",
                "svange/a",
                "--repo",
                "other/b",
                "--name",
                "t",
                "--permissions",
                "contents=write",
            ],
        )
        assert result.exit_code != 0
        assert "same owner" in result.output

    def test_invalid_permissions_errors_before_network(self, monkeypatch):
        monkeypatch.setenv("GITHUB_USERNAME", "u")
        monkeypatch.setenv("GITHUB_PASSWORD", "p")
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "pat",
                "create",
                "--repo",
                "svange/augint-tools",
                "--name",
                "t",
                "--permissions",
                "bogus=nope",
            ],
        )
        assert result.exit_code != 0
        assert "permission" in result.output.lower()

    def test_success_prints_token_to_stdout(self, monkeypatch):
        monkeypatch.setenv("GITHUB_USERNAME", "u")
        monkeypatch.setenv("GITHUB_PASSWORD", "p")

        async def fake_create_pat(request, credentials, otp_provider=None):
            return "ghp_token_secret"

        monkeypatch.setattr("augint_tools.pat.create_pat", fake_create_pat)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "pat",
                "create",
                "--repo",
                "svange/augint-tools",
                "--name",
                "test-token",
                "--permissions",
                "contents=write,metadata=read",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "ghp_token_secret" in result.output

    def test_success_writes_to_env_file_no_stdout_leak(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GITHUB_USERNAME", "u")
        monkeypatch.setenv("GITHUB_PASSWORD", "p")

        async def fake_create_pat(request, credentials, otp_provider=None):
            return "ghp_token_secret"

        monkeypatch.setattr("augint_tools.pat.create_pat", fake_create_pat)

        env_path = tmp_path / ".env"
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "pat",
                "create",
                "--repo",
                "svange/augint-tools",
                "--name",
                "test-token",
                "--permissions",
                "contents=write",
                "--env-file",
                str(env_path),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "ghp_token_secret" not in result.output
        assert "GH_TOKEN" in env_path.read_text()
        assert "ghp_token_secret" in env_path.read_text()

    def test_json_mode_includes_token_in_result(self, monkeypatch):
        monkeypatch.setenv("GITHUB_USERNAME", "u")
        monkeypatch.setenv("GITHUB_PASSWORD", "p")

        async def fake_create_pat(request, credentials, otp_provider=None):
            return "ghp_token_secret"

        monkeypatch.setattr("augint_tools.pat.create_pat", fake_create_pat)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--json",
                "pat",
                "create",
                "--repo",
                "svange/augint-tools",
                "--name",
                "test-token",
                "--permissions",
                "contents=write",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["status"] == "ok"
        assert data["result"]["token"] == "ghp_token_secret"
        assert data["result"]["owner"] == "svange"
        assert data["result"]["repos"] == ["augint-tools"]

    def test_pat_creation_error_exits_non_zero(self, monkeypatch):
        monkeypatch.setenv("GITHUB_USERNAME", "u")
        monkeypatch.setenv("GITHUB_PASSWORD", "p")

        async def fake_create_pat(request, credentials, otp_provider=None):
            raise PatCreationError("authentication failed: bad creds")

        monkeypatch.setattr("augint_tools.pat.create_pat", fake_create_pat)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "pat",
                "create",
                "--repo",
                "svange/augint-tools",
                "--name",
                "t",
                "--permissions",
                "contents=write",
            ],
        )
        assert result.exit_code != 0
        assert "authentication failed" in result.output

    def test_missing_credentials_non_interactive_errors(self, monkeypatch):
        monkeypatch.delenv("GITHUB_USERNAME", raising=False)
        monkeypatch.delenv("GITHUB_PASSWORD", raising=False)
        runner = CliRunner()
        # CliRunner provides an empty stdin; input() raises EOFError, which
        # bubbles up as a non-zero exit.
        result = runner.invoke(
            cli,
            [
                "pat",
                "create",
                "--repo",
                "svange/augint-tools",
                "--name",
                "t",
                "--permissions",
                "contents=write",
            ],
        )
        assert result.exit_code != 0


def asyncio_context(session):
    """Build a fake ``async_client`` context manager that yields *session*."""

    class _Ctx:
        async def __aenter__(self):
            return session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def _factory(*args, **kwargs):
        return _Ctx()

    return _factory
