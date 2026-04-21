"""Tests for team_secrets CLI commands."""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from augint_tools.cli.__main__ import cli


class TestTeamSecretsHelp:
    def test_top_level_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["team-secrets", "--help"])
        assert result.exit_code == 0
        assert "Team shared secrets management" in result.output

    def test_subcommand_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["team-secrets", "woxom", "--help"])
        assert result.exit_code == 0
        assert "setup" in result.output
        assert "doctor" in result.output
        assert "edit" in result.output
        assert "sync" in result.output
        assert "admin" in result.output

    def test_admin_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["team-secrets", "woxom", "admin", "--help"])
        assert result.exit_code == 0
        assert "init-repo" in result.output
        assert "init-project" in result.output
        assert "add-user" in result.output
        assert "remove-user" in result.output
        assert "rotate" in result.output
        assert "decrypt" in result.output
        assert "validate" in result.output


class TestInitRepo:
    def test_init_repo(self, tmp_path):
        runner = CliRunner()
        repo_dir = tmp_path / "woxom-secrets"
        with patch("augint_tools.team_secrets.repo.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.invoke(
                cli,
                ["team-secrets", "woxom", "admin", "init-repo", "--repo", str(repo_dir)],
            )
        assert result.exit_code == 0
        assert "Scaffolded" in result.output
        assert repo_dir.exists()


class TestInitProject:
    def test_init_project(self, tmp_path):
        # Set up a team config
        runner = CliRunner()
        repo_dir = tmp_path / "secrets"
        repo_dir.mkdir()
        (repo_dir / "projects").mkdir()
        (repo_dir / "recipients").mkdir()
        (repo_dir / ".sops.yaml").write_text("creation_rules: []")

        with patch("augint_tools.team_secrets.keys.load_team_config") as mock_config:
            from augint_tools.team_secrets.models import TeamConfig

            mock_config.return_value = TeamConfig(name="woxom", repo_path=repo_dir, username="sam")
            result = runner.invoke(cli, ["team-secrets", "woxom", "admin", "init-project", "myapp"])

        assert result.exit_code == 0
        assert "Initialized project" in result.output
        assert (repo_dir / "projects" / "myapp" / "dev.enc.env").exists()


class TestDoctor:
    def test_doctor_reports_failures(self):
        runner = CliRunner()
        with patch("augint_tools.team_secrets.doctor.is_sops_installed", return_value=False):
            with patch("augint_tools.team_secrets.doctor.is_age_installed", return_value=False):
                with patch("augint_tools.team_secrets.doctor.load_team_config", return_value=None):
                    with patch(
                        "augint_tools.team_secrets.doctor.get_cached_key", return_value=None
                    ):
                        with patch("augint_tools.team_secrets.doctor.subprocess.run") as mock_run:
                            mock_run.side_effect = FileNotFoundError()
                            result = runner.invoke(cli, ["team-secrets", "woxom", "doctor"])

        assert result.exit_code == 2  # ACTION_REQUIRED
        assert "fail" in result.output


class TestEditCommand:
    def test_edit_no_key(self):
        runner = CliRunner()
        with patch("augint_tools.team_secrets.keys.get_cached_key", return_value=None):
            result = runner.invoke(cli, ["team-secrets", "woxom", "edit", "myapp"])
        assert result.exit_code == 1
        assert "setup" in result.output
