"""Tests for gh CLI command group integration."""

import json
from pathlib import Path

from click.testing import CliRunner

from augint_tools.cli.__main__ import cli


class TestGhGroup:
    def test_gh_in_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "gh" in result.output

    def test_gh_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["gh", "--help"])
        assert result.exit_code == 0
        assert "classify" in result.output
        assert "push" in result.output

    def test_sync_in_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "sync" in result.output


class TestClassifyCommand:
    def test_classify_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["gh", "classify", "--help"])
        assert result.exit_code == 0
        assert "secret" in result.output.lower() or "classify" in result.output.lower()

    def test_classify_env_file(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".env").write_text(
                "APP_NAME=myapp\nGH_TOKEN=ghp_abc123\nDB_HOST=localhost\nAWS_PROFILE=default\n"
            )
            result = runner.invoke(cli, ["gh", "classify"])
        assert result.exit_code == 0
        assert "1 secrets" in result.output
        assert "2 variables" in result.output
        assert "1 skipped" in result.output

    def test_classify_json_output(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".env").write_text("APP_NAME=myapp\nGH_TOKEN=ghp_abc123\n")
            result = runner.invoke(cli, ["--json", "gh", "classify"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "ok"
        assert len(data["result"]["secrets"]) == 1
        assert data["result"]["secrets"][0]["key"] == "GH_TOKEN"

    def test_classify_missing_file(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(cli, ["gh", "classify", "nonexistent.env"])
        assert result.exit_code != 0

    def test_classify_empty_file(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".env").write_text("")
            result = runner.invoke(cli, ["gh", "classify"])
        assert result.exit_code == 0
        assert "0 secrets" in result.output

    def test_classify_force_var_flag(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".env").write_text("MY_SECRET=some-value\n")
            result = runner.invoke(cli, ["gh", "classify", "--force-var", "MY_SECRET"])
        assert result.exit_code == 0
        assert "0 secrets" in result.output
        assert "1 variables" in result.output

    def test_classify_force_secret_flag(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".env").write_text("APP_NAME=myapp\n")
            result = runner.invoke(cli, ["gh", "classify", "--force-secret", "APP_NAME"])
        assert result.exit_code == 0
        assert "1 secrets" in result.output
        assert "0 variables" in result.output

    def test_classify_json_variables_have_reasons(self):
        """Variables in JSON output now include reasons (e.g., safe value info)."""
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".env").write_text("APP_NAME=myapp\n")
            result = runner.invoke(cli, ["--json", "gh", "classify"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data["result"]["variables"][0], dict)
        assert "key" in data["result"]["variables"][0]


class TestPushCommand:
    def test_push_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["gh", "push", "--help"])
        assert result.exit_code == 0
        assert "--dry-run" in result.output
        assert "--force-var" in result.output
        assert "--force-secret" in result.output

    def test_push_missing_file(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(cli, ["gh", "push", "nonexistent.env"])
        assert result.exit_code != 0


class TestOutputQuieting:
    """Default mode emits clean lines; --verbose preserves loguru detail; --json stays parseable."""

    def _make_perform_sync_stub(self):
        async def _stub(
            filename,
            dry_run,
            *,
            env_file=None,
            force_var=None,
            force_secret=None,
            quiet_writer=None,
        ):
            if quiet_writer is not None:
                quiet_writer("set FOO_KEY (secret)")
                quiet_writer("set BAR_NAME (var)")
            return {"secrets": ["FOO_KEY"], "variables": ["BAR_NAME"]}

        return _stub

    def test_push_default_output_is_clean(self):
        from unittest.mock import patch

        runner = CliRunner()
        stub = self._make_perform_sync_stub()
        with runner.isolated_filesystem():
            Path(".env").write_text("FOO_KEY=ghp_abc\nBAR_NAME=hello\n")
            with patch("augint_tools.cli.commands.env.perform_sync", new=stub, create=True):
                # patching the module-level import is brittle; patch the source:
                with patch("augint_tools.env.sync.perform_sync", new=stub):
                    result = runner.invoke(cli, ["gh", "push"])

        assert result.exit_code == 0, result.output
        # Clean per-key lines, no timestamps or loguru-style level prefixes
        assert "set FOO_KEY (secret)" in result.output
        assert "set BAR_NAME (var)" in result.output
        assert "DEBUG" not in result.output
        assert "| INFO" not in result.output

    def test_push_json_mode_suppresses_quiet_writer(self):
        from unittest.mock import patch

        runner = CliRunner()
        stub = self._make_perform_sync_stub()
        with runner.isolated_filesystem():
            Path(".env").write_text("FOO_KEY=ghp_abc\n")
            with patch("augint_tools.env.sync.perform_sync", new=stub):
                result = runner.invoke(cli, ["--json", "gh", "push"])

        assert result.exit_code == 0, result.output
        # JSON mode must remain parseable: no quiet_writer narration
        assert "set FOO_KEY (secret)" not in result.output
        data = json.loads(result.output)
        assert data["status"] == "ok"

    def test_push_verbose_preserves_loguru_format(self):
        """With --verbose, loguru emits to stderr in the documented format."""
        from unittest.mock import patch

        from loguru import logger

        async def _stub(
            filename,
            dry_run,
            *,
            env_file=None,
            force_var=None,
            force_secret=None,
            quiet_writer=None,
        ):
            logger.info("Creating secret VERBOSE_KEY...")
            return {"secrets": ["VERBOSE_KEY"], "variables": []}

        runner = CliRunner(mix_stderr=False)
        with runner.isolated_filesystem():
            Path(".env").write_text("VERBOSE_KEY=ghp_abc\n")
            with patch("augint_tools.env.sync.perform_sync", new=_stub):
                result = runner.invoke(cli, ["gh", "push", "--verbose"])

        assert result.exit_code == 0, result.output
        # Verbose loguru format includes a level token (DEBUG/INFO) and the message
        assert "Creating secret VERBOSE_KEY" in (result.stderr or "")
        # Restore loguru to a quiet state for following tests
        logger.remove()
