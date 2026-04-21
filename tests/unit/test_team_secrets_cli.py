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

    def test_org_flag(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["team-secrets", "woxom", "--org", "my-org", "--help"])
        assert result.exit_code == 0


class TestInitProject:
    def test_init_project_auto_detect(self, tmp_path):
        runner = CliRunner()
        repo_dir = tmp_path / "secrets"
        repo_dir.mkdir()
        (repo_dir / "projects").mkdir()

        with patch(
            "augint_tools.cli.commands.team_secrets._require_project", return_value="my-app"
        ):
            with patch("augint_tools.team_secrets.checkout.ephemeral_checkout") as mock_checkout:
                mock_checkout.return_value.__enter__ = MagicMock(return_value=repo_dir)
                mock_checkout.return_value.__exit__ = MagicMock(return_value=False)
                result = runner.invoke(cli, ["team-secrets", "woxom", "admin", "init-project"])

        assert result.exit_code == 0
        assert "Initialized project" in result.output


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
                            with patch(
                                "augint_tools.team_secrets.doctor.detect_project_name",
                                return_value=None,
                            ):
                                result = runner.invoke(cli, ["team-secrets", "woxom", "doctor"])

        assert result.exit_code == 2  # ACTION_REQUIRED
        assert "fail" in result.output


class TestEditCommand:
    def test_edit_no_key(self):
        runner = CliRunner()
        with patch("augint_tools.team_secrets.keys.get_cached_key", return_value=None):
            result = runner.invoke(cli, ["team-secrets", "woxom", "edit", "--project", "myapp"])
        assert result.exit_code == 1
        assert "setup" in result.output
