"""Tests for team_secrets.sync module."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import click
import pytest

from augint_tools.team_secrets.models import ConflictEntry
from augint_tools.team_secrets.sync import (
    _truncate,
    compute_merge,
    parse_dotenv_content,
    perform_team_sync,
    resolve_conflicts_interactive,
    serialize_dotenv,
)


class TestParseDotenvContent:
    def test_simple(self):
        content = "KEY=value\nOTHER=stuff\n"
        result = parse_dotenv_content(content)
        assert result == {"KEY": "value", "OTHER": "stuff"}

    def test_quoted_values(self):
        content = "KEY=\"hello world\"\nSINGLE='quoted'\n"
        result = parse_dotenv_content(content)
        assert result == {"KEY": "hello world", "SINGLE": "quoted"}

    def test_comments_and_blanks(self):
        content = "# comment\n\nKEY=value\n\n# another\nOTHER=x\n"
        result = parse_dotenv_content(content)
        assert result == {"KEY": "value", "OTHER": "x"}

    def test_export_prefix(self):
        content = "export KEY=value\nexport OTHER=stuff\n"
        result = parse_dotenv_content(content)
        assert result == {"KEY": "value", "OTHER": "stuff"}

    def test_inline_comments(self):
        content = "KEY=value # this is a comment\n"
        result = parse_dotenv_content(content)
        assert result == {"KEY": "value"}

    def test_no_value(self):
        content = "KEY=\n"
        result = parse_dotenv_content(content)
        assert result == {"KEY": ""}

    def test_equals_in_value(self):
        content = "URL=postgres://user:pass@host/db?sslmode=require\n"
        result = parse_dotenv_content(content)
        assert result["URL"] == "postgres://user:pass@host/db?sslmode=require"

    def test_malformed_lines_skipped(self):
        content = "GOOD=value\nno_equals_here\nALSO_GOOD=x\n"
        result = parse_dotenv_content(content)
        assert result == {"GOOD": "value", "ALSO_GOOD": "x"}


class TestSerializeDotenv:
    def test_simple(self):
        data = {"KEY": "value", "OTHER": "stuff"}
        result = serialize_dotenv(data)
        assert "KEY=value\n" in result
        assert "OTHER=stuff\n" in result

    def test_quoting(self):
        data = {"KEY": "hello world"}
        result = serialize_dotenv(data)
        assert 'KEY="hello world"' in result

    def test_empty_dict(self):
        assert serialize_dotenv({}) == ""

    def test_sorted_keys(self):
        data = {"Z": "1", "A": "2", "M": "3"}
        result = serialize_dotenv(data)
        lines = result.strip().split("\n")
        assert lines[0].startswith("A=")
        assert lines[1].startswith("M=")
        assert lines[2].startswith("Z=")


class TestComputeMerge:
    def test_no_conflicts(self):
        team = {"A": "1", "B": "2"}
        local = {"A": "1", "B": "2"}
        result = compute_merge(team, local)
        assert result.conflicts == []
        assert result.unchanged == ["A", "B"]
        assert result.merged == {"A": "1", "B": "2"}

    def test_additions(self):
        team = {"A": "1"}
        local = {"A": "1", "NEW": "new_val"}
        result = compute_merge(team, local)
        assert result.additions == ["NEW"]
        assert result.merged["NEW"] == "new_val"
        assert result.conflicts == []

    def test_team_only_keys(self):
        team = {"A": "1", "TEAM_ONLY": "secret"}
        local = {"A": "1"}
        result = compute_merge(team, local)
        assert result.merged["TEAM_ONLY"] == "secret"
        assert result.conflicts == []

    def test_conflicts(self):
        team = {"A": "team_val"}
        local = {"A": "local_val"}
        result = compute_merge(team, local)
        assert len(result.conflicts) == 1
        assert result.conflicts[0].key == "A"
        assert result.conflicts[0].team_value == "team_val"
        assert result.conflicts[0].local_value == "local_val"
        # Conflicting key not in merged
        assert "A" not in result.merged

    def test_mixed(self):
        team = {"SHARED": "same", "CONFLICT": "team_v", "TEAM_ONLY": "t"}
        local = {"SHARED": "same", "CONFLICT": "local_v", "LOCAL_ONLY": "l"}
        result = compute_merge(team, local)
        assert result.unchanged == ["SHARED"]
        assert result.additions == ["LOCAL_ONLY"]
        assert len(result.conflicts) == 1
        assert result.conflicts[0].key == "CONFLICT"
        assert result.merged["SHARED"] == "same"
        assert result.merged["TEAM_ONLY"] == "t"
        assert result.merged["LOCAL_ONLY"] == "l"


class TestTruncate:
    def test_short_string_untouched(self):
        assert _truncate("hello", 10) == "hello"

    def test_long_string_gets_ellipsis(self):
        assert _truncate("a" * 100, 10) == "aaaaaaa..."


class TestResolveConflictsInteractive:
    def _conflict(self, key="K", local="lv", team="tv") -> ConflictEntry:
        return ConflictEntry(key=key, local_value=local, team_value=team)

    def test_keep_team(self):
        with patch("augint_tools.team_secrets.sync.click.prompt", return_value="t"):
            resolved = resolve_conflicts_interactive([self._conflict()])
        assert resolved == {"K": "tv"}

    def test_keep_local(self):
        with patch("augint_tools.team_secrets.sync.click.prompt", return_value="L"):
            resolved = resolve_conflicts_interactive([self._conflict()])
        assert resolved == {"K": "lv"}

    def test_custom_value(self):
        with patch(
            "augint_tools.team_secrets.sync.click.prompt",
            side_effect=["c", "custom-val"],
        ):
            resolved = resolve_conflicts_interactive([self._conflict()])
        assert resolved == {"K": "custom-val"}


class TestPerformTeamSync:
    """The full sync workflow: patches decrypt/encrypt/commit so no subprocess runs."""

    def _setup_repo(self, tmp_path, *, team_body: str = "SHARED=t\nTEAM_ONLY=t\n") -> Path:
        repo = tmp_path / "team-repo"
        (repo / "secrets" / "proj" / "env").mkdir(parents=True)
        enc = repo / "secrets" / "proj" / "env" / "dev.enc.env"
        enc.write_text("<encrypted>")
        # Record the expected path for assertions.
        return repo

    def test_raises_when_encrypted_file_missing(self, tmp_path):
        with patch(
            "augint_tools.team_secrets.repo.get_encrypted_env_path",
            return_value=tmp_path / "missing.enc.env",
        ):
            with pytest.raises(click.ClickException, match="No encrypted file"):
                perform_team_sync(
                    team_repo_path=tmp_path,
                    project="p",
                    env="dev",
                    key_file=tmp_path / "key",
                )

    def test_no_local_env_diff_only(self, tmp_path):
        enc = tmp_path / "e.enc.env"
        enc.write_text("<enc>")
        with (
            patch("augint_tools.team_secrets.repo.get_encrypted_env_path", return_value=enc),
            patch(
                "augint_tools.team_secrets.sync.decrypt_file",
                return_value="A=1\nB=2\n",
            ),
        ):
            result = perform_team_sync(
                team_repo_path=tmp_path,
                project="p",
                env="dev",
                key_file=tmp_path / "key",
                local_env_path=tmp_path / "absent.env",
                diff_only=True,
            )
        assert result["diff"] == "No local .env to compare"
        assert set(result["team_keys"]) == {"A", "B"}

    def test_no_local_env_writes_local_dry_run(self, tmp_path):
        enc = tmp_path / "e.enc.env"
        enc.write_text("<enc>")
        target = tmp_path / "fresh.env"
        with (
            patch("augint_tools.team_secrets.repo.get_encrypted_env_path", return_value=enc),
            patch(
                "augint_tools.team_secrets.sync.decrypt_file",
                return_value="A=1\n",
            ),
        ):
            result = perform_team_sync(
                team_repo_path=tmp_path,
                project="p",
                env="dev",
                key_file=tmp_path / "key",
                local_env_path=target,
                dry_run=True,
                write_local_env=True,
            )
        assert result == {"action": "wrote_local", "keys_written": 1, "dry_run": True}
        # Dry run -> nothing written.
        assert not target.exists()

    def test_no_local_env_writes_local_for_real(self, tmp_path):
        enc = tmp_path / "e.enc.env"
        enc.write_text("<enc>")
        target = tmp_path / "fresh.env"
        with (
            patch("augint_tools.team_secrets.repo.get_encrypted_env_path", return_value=enc),
            patch(
                "augint_tools.team_secrets.sync.decrypt_file",
                return_value="A=1\n",
            ),
        ):
            result = perform_team_sync(
                team_repo_path=tmp_path,
                project="p",
                env="dev",
                key_file=tmp_path / "key",
                local_env_path=target,
                write_local_env=True,
            )
        assert result["action"] == "wrote_local"
        assert target.read_text().strip() == "A=1"

    def test_no_local_env_default(self, tmp_path):
        enc = tmp_path / "e.enc.env"
        enc.write_text("<enc>")
        with (
            patch("augint_tools.team_secrets.repo.get_encrypted_env_path", return_value=enc),
            patch(
                "augint_tools.team_secrets.sync.decrypt_file",
                return_value="A=1\nB=2\n",
            ),
        ):
            result = perform_team_sync(
                team_repo_path=tmp_path,
                project="p",
                env="dev",
                key_file=tmp_path / "key",
                local_env_path=tmp_path / "missing.env",
            )
        assert result["action"] == "no_local_env"
        assert set(result["team_keys"]) == {"A", "B"}

    def test_diff_only_with_conflicts_and_additions(self, tmp_path):
        enc = tmp_path / "e.enc.env"
        enc.write_text("<enc>")
        local_env = tmp_path / ".env"
        local_env.write_text("A=local\nNEW=added\n")
        with (
            patch("augint_tools.team_secrets.repo.get_encrypted_env_path", return_value=enc),
            patch(
                "augint_tools.team_secrets.sync.decrypt_file",
                return_value="A=team\nB=same\n",
            ),
        ):
            result = perform_team_sync(
                team_repo_path=tmp_path,
                project="p",
                env="dev",
                key_file=tmp_path / "key",
                local_env_path=local_env,
                diff_only=True,
            )
        assert result["additions"] == ["NEW"]
        assert len(result["conflicts"]) == 1
        assert result["conflicts"][0]["key"] == "A"

    def test_non_interactive_conflicts_return_action_required(self, tmp_path, monkeypatch):
        enc = tmp_path / "e.enc.env"
        enc.write_text("<enc>")
        local_env = tmp_path / ".env"
        local_env.write_text("A=local\n")
        # Force non-tty path.
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        with (
            patch("augint_tools.team_secrets.repo.get_encrypted_env_path", return_value=enc),
            patch(
                "augint_tools.team_secrets.sync.decrypt_file",
                return_value="A=team\n",
            ),
        ):
            result = perform_team_sync(
                team_repo_path=tmp_path,
                project="p",
                env="dev",
                key_file=tmp_path / "key",
                local_env_path=local_env,
            )
        assert result["status"] == "action-required"
        assert result["conflicts"][0]["key"] == "A"

    def test_full_sync_writes_commits_pushes(self, tmp_path):
        enc = tmp_path / "e.enc.env"
        enc.write_text("<enc>")
        local_env = tmp_path / ".env"
        local_env.write_text("SHARED=same\nNEW=added\n")
        commit_mock = MagicMock()
        encrypt_mock = MagicMock()
        with (
            patch("augint_tools.team_secrets.repo.get_encrypted_env_path", return_value=enc),
            patch(
                "augint_tools.team_secrets.sync.decrypt_file",
                return_value="SHARED=same\nTEAM_ONLY=t\n",
            ),
            patch("augint_tools.team_secrets.sync.encrypt_content", encrypt_mock),
            patch("augint_tools.team_secrets.repo.commit_and_push", commit_mock),
        ):
            result = perform_team_sync(
                team_repo_path=tmp_path,
                project="p",
                env="dev",
                key_file=tmp_path / "key",
                local_env_path=local_env,
                write_local_env=True,
            )
        assert "encrypted" in result["actions"]
        assert "committed and pushed" in result["actions"]
        assert any(a.endswith("wrote local .env") for a in result["actions"])
        assert result["additions"] == ["NEW"]
        assert result["dry_run"] is False
        commit_mock.assert_called_once()
        encrypt_mock.assert_called_once()

    def test_no_push_mode_commits_without_pushing(self, tmp_path):
        enc = tmp_path / "e.enc.env"
        enc.write_text("<enc>")
        local_env = tmp_path / ".env"
        local_env.write_text("SHARED=same\n")
        with (
            patch("augint_tools.team_secrets.repo.get_encrypted_env_path", return_value=enc),
            patch(
                "augint_tools.team_secrets.sync.decrypt_file",
                return_value="SHARED=same\n",
            ),
            patch("augint_tools.team_secrets.sync.encrypt_content"),
            patch("subprocess.run") as mock_sub,
        ):
            result = perform_team_sync(
                team_repo_path=tmp_path,
                project="p",
                env="dev",
                key_file=tmp_path / "key",
                local_env_path=local_env,
                no_push=True,
            )
        assert "committed (not pushed)" in result["actions"]
        # Should have invoked `git add` + `git commit`, no `git push`.
        calls = [c.args[0] for c in mock_sub.call_args_list]
        assert ["git", "add", "-A"] in calls
        assert any(c[:2] == ["git", "commit"] for c in calls)
        assert not any("push" in c for c in calls)

    def test_dry_run_skips_api_calls(self, tmp_path):
        enc = tmp_path / "e.enc.env"
        enc.write_text("<enc>")
        local_env = tmp_path / ".env"
        local_env.write_text("SHARED=same\n")
        encrypt_mock = MagicMock()
        commit_mock = MagicMock()
        with (
            patch("augint_tools.team_secrets.repo.get_encrypted_env_path", return_value=enc),
            patch(
                "augint_tools.team_secrets.sync.decrypt_file",
                return_value="SHARED=same\n",
            ),
            patch("augint_tools.team_secrets.sync.encrypt_content", encrypt_mock),
            patch("augint_tools.team_secrets.repo.commit_and_push", commit_mock),
        ):
            result = perform_team_sync(
                team_repo_path=tmp_path,
                project="p",
                env="dev",
                key_file=tmp_path / "key",
                local_env_path=local_env,
                dry_run=True,
                write_local_env=True,
            )
        assert result["dry_run"] is True
        encrypt_mock.assert_not_called()
        commit_mock.assert_not_called()
        # Under dry-run the action log records "would encrypt" rather than "encrypted".
        assert "would encrypt" in result["actions"]

    def test_interactive_conflict_resolution(self, tmp_path, monkeypatch):
        enc = tmp_path / "e.enc.env"
        enc.write_text("<enc>")
        local_env = tmp_path / ".env"
        local_env.write_text("A=local\n")
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        with (
            patch("augint_tools.team_secrets.repo.get_encrypted_env_path", return_value=enc),
            patch(
                "augint_tools.team_secrets.sync.decrypt_file",
                return_value="A=team\n",
            ),
            patch(
                "augint_tools.team_secrets.sync.resolve_conflicts_interactive",
                return_value={"A": "final"},
            ),
            patch("augint_tools.team_secrets.sync.encrypt_content"),
            patch("augint_tools.team_secrets.repo.commit_and_push"),
        ):
            result = perform_team_sync(
                team_repo_path=tmp_path,
                project="p",
                env="dev",
                key_file=tmp_path / "key",
                local_env_path=local_env,
            )
        assert result["conflicts_resolved"] == 1
