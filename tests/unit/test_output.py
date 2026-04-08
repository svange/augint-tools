"""Tests for output module."""

import json

from augint_tools.output import create_error_response, emit_json, emit_output


class TestOutputFormatter:
    def test_emit_json(self, capsys):
        """Test JSON output."""
        data = {"command": "test", "status": "ok"}
        emit_json(data)

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["command"] == "test"
        assert output["status"] == "ok"
        assert "timestamp" in output

    def test_emit_output_json(self, capsys):
        """Test emit_output with JSON."""
        emit_output(
            command="status",
            scope="repo",
            as_json=True,
            test_data="test_value",
        )

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["command"] == "status"
        assert output["scope"] == "repo"
        assert output["status"] == "ok"
        assert output["test_data"] == "test_value"

    def test_emit_output_human(self, capsys):
        """Test emit_output with human-readable format."""
        emit_output(
            command="status",
            scope="repo",
            as_json=False,
        )

        captured = capsys.readouterr()
        assert "status" in captured.out

    def test_create_error_response(self):
        """Test creating error response."""
        response = create_error_response(
            command="test",
            scope="repo",
            error="Something went wrong",
        )

        assert response["command"] == "test"
        assert response["scope"] == "repo"
        assert response["status"] == "error"
        assert response["error"] == "Something went wrong"
