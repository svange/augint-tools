"""Tests for CLI module."""

import json
from unittest.mock import patch

from click.testing import CliRunner

from augint_tools.cli.__main__ import cli


class TestCli:
    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "CLI for AI-assisted repository and workspace workflows." in result.output
        assert "init" in result.output
        assert "repo" in result.output
        assert "workspace" in result.output

    def test_global_flags_in_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert "--json" in result.output
        assert "--actionable" in result.output
        assert "--summary" in result.output

    def test_repo_subgroups(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["repo", "--help"])
        assert result.exit_code == 0
        assert "inspect" in result.output
        assert "status" in result.output
        assert "issues" in result.output
        assert "branch" in result.output
        assert "check" in result.output
        assert "submit" in result.output
        assert "ci" in result.output
        assert "promote" in result.output
        assert "rollback" in result.output
        assert "health" in result.output

    def test_repo_inspect(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["repo", "inspect"])
        assert result.exit_code == 0
        assert "python" in result.output.lower()

    def test_repo_inspect_json(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "repo", "inspect"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["command"] == "repo inspect"
        assert data["scope"] == "repo"
        assert data["status"] == "ok"
        assert "result" in data
        assert data["result"]["language"] == "python"

    def test_repo_status(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["repo", "status"])
        assert result.exit_code == 0
        assert "status" in result.output.lower()

    def test_repo_status_json(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "repo", "status"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["command"] == "repo status"
        assert data["scope"] == "repo"
        assert "result" in data
        assert "repo" in data["result"]

    def test_repo_check_plan(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["repo", "check", "plan"])
        assert result.exit_code == 0
        assert "phases" in result.output.lower() or "preset" in result.output.lower()

    def test_repo_check_plan_json(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "repo", "check", "plan", "--preset", "full"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["result"]["preset"] == "full"
        assert len(data["result"]["phases"]) > 0

    def test_workspace_subgroups(self):
        """Test that workspace subcommands are available."""
        runner = CliRunner()
        result = runner.invoke(cli, ["workspace", "--help"])
        assert result.exit_code == 0
        assert "inspect" in result.output

    def test_workspace_status_no_config(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["workspace", "status"])
        assert result.exit_code == 1

    def test_workspace_status_json_no_config(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "workspace", "status"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["status"] == "error"

    def test_workspace_standardize_requires_verify(self, tmp_path, monkeypatch):
        """Invoking without --verify must error explicitly; no other mode is implemented yet."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "workspace", "standardize"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["status"] == "error"
        assert "--verify" in data["summary"]

    def test_workspace_standardize_missing_config(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "workspace", "standardize", "--verify"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["status"] == "error"
        assert "workspace" in data["summary"].lower()

    def test_workspace_standardize_verify_clean(self, tmp_path, monkeypatch):
        """Clean run across two children exits 0 with status=ok."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "lib-a").mkdir()
        (tmp_path / "lib-b").mkdir()
        (tmp_path / "workspace.yaml").write_text(
            "workspace:\n"
            "  name: test-ws\n"
            "  repos_dir: .\n"
            "repos:\n"
            "  - name: lib-a\n"
            "    path: lib-a\n"
            "    url: https://example.invalid/lib-a.git\n"
            "    repo_type: library\n"
            "    base_branch: main\n"
            "    pr_target_branch: main\n"
            "  - name: lib-b\n"
            "    path: lib-b\n"
            "    url: https://example.invalid/lib-b.git\n"
            "    repo_type: library\n"
            "    base_branch: main\n"
            "    pr_target_branch: main\n"
            "    depends_on: [lib-a]\n"
        )

        clean_stdout = (
            "[PASS] detect: python/library\n"
            "[PASS] pipeline: all jobs present\n"
            "[PASS] precommit: matches template\n"
            "[PASS] renovate: matches template\n"
            "[PASS] release: matches canon\n"
            "[PASS] dotfiles: match canon\n"
            "[PASS] repo_settings: all settings match\n"
            "[PASS] rulesets: library\n"
            "[PASS] oidc: matches canon\n"
        )

        runner = CliRunner()
        with patch(
            "augint_tools.workspace_standardize._run_ai_shell_verify",
            return_value=(0, clean_stdout, ""),
        ):
            result = runner.invoke(cli, ["--json", "workspace", "standardize", "--verify"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["command"] == "workspace standardize --verify"
        assert data["scope"] == "workspace"
        assert data["status"] == "ok"
        assert data["result"]["order"] == ["lib-a", "lib-b"]
        assert data["result"]["order_source"] == "depends_on"
        assert data["result"]["aggregate"]["repos_clean"] == 2
        assert data["result"]["aggregate"]["repos_drift"] == 0

    def test_workspace_standardize_verify_drift(self, tmp_path, monkeypatch):
        """Drift across children exits 1 with status=drift and the right next_action."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "lib-a").mkdir()
        (tmp_path / "workspace.yaml").write_text(
            "workspace:\n"
            "  name: test-ws\n"
            "  repos_dir: .\n"
            "repos:\n"
            "  - name: lib-a\n"
            "    path: lib-a\n"
            "    url: https://example.invalid/lib-a.git\n"
            "    repo_type: library\n"
            "    base_branch: main\n"
            "    pr_target_branch: main\n"
        )

        drift_stdout = (
            "[PASS] detect: python/library\n"
            "[DRIFT] pipeline: missing: Code quality\n"
            "[PASS] precommit: matches template\n"
            "[DRIFT] renovate: renovate.json5 differs\n"
            "[PASS] release: matches canon\n"
            "[PASS] dotfiles: match canon\n"
            "[PASS] repo_settings: all settings match\n"
            "[PASS] rulesets: library\n"
            "[PASS] oidc: matches canon\n"
        )

        runner = CliRunner()
        with patch(
            "augint_tools.workspace_standardize._run_ai_shell_verify",
            return_value=(0, drift_stdout, ""),
        ):
            result = runner.invoke(cli, ["--json", "workspace", "standardize", "--verify"])

        assert result.exit_code == 1, result.output
        data = json.loads(result.output)
        assert data["status"] == "drift"
        assert data["result"]["aggregate"]["total_sections_drift"] == 2
        assert any("ai-workspace-standardize" in a for a in data["next_actions"])

    def test_stub_commands(self):
        """Test that P1/P2 stubs emit the right output."""
        runner = CliRunner()
        stubs = [
            ["repo", "promote"],
            ["repo", "rollback", "plan"],
            ["repo", "rollback", "apply"],
            ["repo", "health"],
            ["workspace", "graph"],
            ["workspace", "update"],
        ]
        for args in stubs:
            result = runner.invoke(cli, args)
            assert "not yet implemented" in result.output.lower(), f"Stub failed for {args}"
