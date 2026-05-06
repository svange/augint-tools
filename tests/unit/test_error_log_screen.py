from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from augint_tools.dashboard.screens.error_log import ErrorLogScreen
from augint_tools.dashboard.state import AppState, ErrorEntry


def test_render_errors_empty_state():
    screen = ErrorLogScreen(AppState())
    rendered = screen._render_errors()
    assert "no errors recorded" in rendered.plain


def test_render_errors_with_entries():
    state = AppState(
        errors=[
            ErrorEntry(
                timestamp=datetime(2026, 1, 1, 12, 0, tzinfo=UTC), source="refresh", message="first"
            ),
            ErrorEntry(
                timestamp=datetime(2026, 1, 1, 12, 1, tzinfo=UTC), source="ui", message="second"
            ),
        ]
    )
    screen = ErrorLogScreen(state)
    rendered = screen._render_errors()
    assert "[ui]" in rendered.plain
    assert "[refresh]" in rendered.plain
    assert rendered.plain.index("[ui]") < rendered.plain.index("[refresh]")


def test_on_button_pressed_clear_and_close(monkeypatch):
    state = AppState(errors=[ErrorEntry(timestamp=datetime.now(UTC), source="ui", message="boom")])
    screen = ErrorLogScreen(state)
    updated = MagicMock()
    monkeypatch.setattr(
        screen, "query_one", lambda *_args, **_kwargs: SimpleNamespace(update=updated)
    )
    dismissed = MagicMock()
    monkeypatch.setattr(screen, "dismiss", dismissed)

    clear_event = SimpleNamespace(button=SimpleNamespace(id="clear-errors"))
    screen.on_button_pressed(clear_event)
    assert state.errors == []
    updated.assert_called_once()

    close_event = SimpleNamespace(button=SimpleNamespace(id="close-errors"))
    screen.on_button_pressed(close_event)
    dismissed.assert_called()


def test_action_dismiss_calls_dismiss(monkeypatch):
    screen = ErrorLogScreen(AppState())
    dismissed = MagicMock()
    monkeypatch.setattr(screen, "dismiss", dismissed)
    screen.action_dismiss()
    dismissed.assert_called_once()
