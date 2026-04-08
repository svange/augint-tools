"""Tests for output module."""

import json

from augint_tools.output import CommandResponse, ExitCode, emit_response


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
