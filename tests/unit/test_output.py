"""Tests for output module."""

import json

import pytest

from augint_tools.output import CommandResponse, ExitCode, emit_response
from augint_tools.output.formatter import emit_error, emit_warning


class TestExitCode:
    def test_exit_codes(self):
        assert ExitCode.SUCCESS == 0
        assert ExitCode.FAILURE == 1
        assert ExitCode.ACTION_REQUIRED == 2
        assert ExitCode.BLOCKED == 3
        assert ExitCode.PARTIAL == 4


class TestCommandResponse:
    def test_ok_response(self):
        r = CommandResponse.ok("repo status", "repo", "All clean", {"branch": "main"})
        assert r.status == "ok"
        assert r.exit_code == 0
        assert r.summary == "All clean"
        assert r.result["branch"] == "main"

    def test_error_response(self):
        r = CommandResponse.error("repo submit", "repo", "Not in a git repo")
        assert r.status == "error"
        assert r.exit_code == 1
        assert r.errors == ["Not in a git repo"]

    def test_to_dict(self):
        r = CommandResponse.ok("test", "repo", "done")
        d = r.to_dict()
        assert d["command"] == "test"
        assert d["scope"] == "repo"
        assert d["status"] == "ok"
        assert d["summary"] == "done"
        assert d["next_actions"] == []
        assert d["warnings"] == []
        assert d["errors"] == []

    def test_exit_code_mapping(self):
        assert CommandResponse(command="", scope="", status="ok", summary="").exit_code == 0
        assert CommandResponse(command="", scope="", status="error", summary="").exit_code == 1
        assert (
            CommandResponse(command="", scope="", status="action-required", summary="").exit_code
            == 2
        )
        assert CommandResponse(command="", scope="", status="blocked", summary="").exit_code == 3
        assert CommandResponse(command="", scope="", status="partial", summary="").exit_code == 4


class TestEmitResponse:
    def test_json_output(self, capsys):
        r = CommandResponse.ok("test", "repo", "done", {"key": "value"})
        emit_response(r, json_mode=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["command"] == "test"
        assert data["status"] == "ok"
        assert data["result"]["key"] == "value"
        assert "timestamp" in data

    def test_human_output(self, capsys):
        r = CommandResponse.ok("test", "repo", "done")
        emit_response(r, json_mode=False)
        captured = capsys.readouterr()
        assert "test" in captured.out
        assert "done" in captured.out

    def test_actionable_suppresses_ok(self, capsys):
        r = CommandResponse.ok("test", "repo", "all good")
        emit_response(r, actionable=True)
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_actionable_shows_warnings(self, capsys):
        r = CommandResponse(
            command="test",
            scope="repo",
            status="ok",
            summary="mostly ok",
            warnings=["something off"],
        )
        emit_response(r, actionable=True)
        captured = capsys.readouterr()
        assert "mostly ok" in captured.out

    def test_summary_mode(self, capsys):
        r = CommandResponse(
            command="test",
            scope="repo",
            status="ok",
            summary="done",
            next_actions=["deploy"],
            result={"detail": "should not appear"},
        )
        emit_response(r, summary_only=True)
        captured = capsys.readouterr()
        assert "done" in captured.out
        assert "deploy" in captured.out
        assert "detail" not in captured.out

    def test_summary_json(self, capsys):
        r = CommandResponse.ok("test", "repo", "done", {"detail": "hidden"})
        emit_response(r, json_mode=True, summary_only=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "result" not in data
        assert data["summary"] == "done"

    def test_human_emits_warnings_errors_and_next(self, capsys):
        r = CommandResponse(
            command="test",
            scope="repo",
            status="error",
            summary="broke",
            warnings=["warn-a"],
            errors=["err-b"],
            next_actions=["retry"],
        )
        emit_response(r)
        captured = capsys.readouterr()
        # summary on stdout
        assert "broke" in captured.out
        assert "Next: retry" in captured.out
        # warnings / errors go to stderr
        assert "Warning: warn-a" in captured.err
        assert "err-b" in captured.err

    def test_summary_mode_without_next(self, capsys):
        r = CommandResponse(command="test", scope="repo", status="ok", summary="q")
        emit_response(r, summary_only=True)
        captured = capsys.readouterr()
        assert "q" in captured.out
        assert "Next:" not in captured.out

    @pytest.mark.parametrize(
        "status,label",
        [
            ("ok", "[ok]"),
            ("error", "[error]"),
            ("action-required", "[action]"),
            ("blocked", "[blocked]"),
            ("partial", "[partial]"),
            ("weird", "[weird]"),
        ],
    )
    def test_status_icon_renders(self, capsys, status, label):
        r = CommandResponse(command="c", scope="s", status=status, summary="x")
        emit_response(r)
        captured = capsys.readouterr()
        assert label in captured.out


class TestRegistryFormatters:
    def test_workspace_status_formatter(self, capsys):
        r = CommandResponse.ok(
            "workspace status",
            "workspace",
            "ok",
            {
                "workspace": {"name": "demo"},
                "repos": [
                    {"name": "a", "present": True, "branch": "main", "dirty": False},
                    {"name": "b", "present": True, "branch": "dev", "dirty": True},
                    {"name": "c", "present": False},
                ],
            },
        )
        emit_response(r)
        out = capsys.readouterr().out
        assert "demo" in out
        assert "Repositories: 3" in out
        assert "a (main)" in out
        assert "b (dev)" in out
        assert "c" in out and "missing" in out

    def test_branch_formatter(self, capsys):
        r = CommandResponse.ok("workspace branch", "workspace", "ok", {"branch": "feat/x"})
        emit_response(r)
        assert "feat/x" in capsys.readouterr().out

    def test_branch_formatter_no_branch_key(self, capsys):
        r = CommandResponse.ok("workspace branch", "workspace", "ok", {})
        emit_response(r)
        # No crash; branch line simply absent.
        assert "Branch" not in capsys.readouterr().out

    def test_check_formatter(self, capsys):
        r = CommandResponse.ok(
            "workspace check",
            "workspace",
            "ok",
            {
                "phases": [
                    {"phase": "lint", "status": "passed", "duration_seconds": 1.234},
                    {
                        "phase": "test",
                        "status": "failed",
                        "duration_seconds": 2.0,
                        "failures": ["oops"],
                    },
                ]
            },
        )
        emit_response(r)
        out = capsys.readouterr().out
        assert "lint" in out and "1.2s" in out
        assert "test" in out and "2.0s" in out
        assert "oops" in out

    def test_foreach_formatter_success_and_failure(self, capsys):
        r = CommandResponse.ok(
            "workspace foreach",
            "workspace",
            "ok",
            {
                "results": [
                    {"repo": "a", "success": True, "output": "hello\n\nworld"},
                    {"name": "b", "success": False, "exit_code": 2},
                ]
            },
        )
        emit_response(r)
        out = capsys.readouterr().out
        assert "a" in out and "b" in out
        assert "hello" in out and "world" in out
        assert "exit 2" in out

    def test_ide_info_formatter(self, capsys):
        r = CommandResponse.ok(
            "ide info",
            "ide",
            "ok",
            {
                "project_name": "proj",
                "venv_path": "/v",
                "python_version": "3.12",
                "sdk_name": "Python 3.12 (proj)",
                "iml_path": "/p/proj.iml",
                "idea_dir_exists": True,
                "windows_project_dir": "C:\\proj",
                "jb_options_dir": "/jb",
                "gh_token_present": True,
            },
        )
        emit_response(r)
        out = capsys.readouterr().out
        assert "proj" in out
        assert "Python  : 3.12" in out
        assert "proj.iml" in out
        assert "C:\\proj" in out
        assert "/jb" in out
        assert "set" in out  # GH_TOKEN present

    def test_ide_info_formatter_missing_optional(self, capsys):
        r = CommandResponse.ok(
            "ide info",
            "ide",
            "ok",
            {
                "project_name": "proj",
                "venv_path": "/v",
                "python_version": "3.12",
                "sdk_name": "sdk",
                "iml_path": None,
                "idea_dir_exists": False,
                "gh_token_present": False,
            },
        )
        emit_response(r)
        out = capsys.readouterr().out
        assert "(none)" in out
        assert "missing" in out
        assert "not set" in out

    def test_ide_setup_formatter(self, capsys):
        r = CommandResponse.ok("ide setup", "ide", "ok", {"sdk_name": "sdk-xyz"})
        emit_response(r)
        assert "sdk-xyz" in capsys.readouterr().out

    def test_ide_setup_formatter_without_sdk(self, capsys):
        r = CommandResponse.ok("ide setup", "ide", "ok", {})
        emit_response(r)
        assert "SDK name to use" not in capsys.readouterr().out

    def test_env_classify_formatter(self, capsys):
        r = CommandResponse.ok(
            "gh classify",
            "env",
            "ok",
            {
                "secrets": [{"key": "API_KEY", "reasons": ["high entropy"]}],
                "variables": ["LOG_LEVEL"],
                "skipped": ["COMMENT"],
            },
        )
        emit_response(r)
        out = capsys.readouterr().out
        assert "API_KEY" in out and "high entropy" in out
        assert "LOG_LEVEL" in out
        assert "COMMENT" in out

    def test_env_classify_empty(self, capsys):
        r = CommandResponse.ok("gh classify", "env", "ok", {})
        emit_response(r)
        # no Secrets/Variables/Skipped headers when all empty
        out = capsys.readouterr().out
        assert "Secrets:" not in out
        assert "Variables:" not in out
        assert "Skipped:" not in out


class TestEmitHelpers:
    def test_emit_warning_goes_to_stderr(self, capsys):
        emit_warning("heads up")
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "Warning: heads up" in captured.err

    def test_emit_error_without_exit(self, capsys):
        emit_error("uh oh")
        captured = capsys.readouterr()
        assert "Error: uh oh" in captured.err

    def test_emit_error_with_exit_code(self, capsys):
        with pytest.raises(SystemExit) as exc:
            emit_error("fatal", exit_code=3)
        assert exc.value.code == 3
        assert "fatal" in capsys.readouterr().err
