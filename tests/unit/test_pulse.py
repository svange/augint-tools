"""Tests for the multi-phase pulse system (replaces the binary flash toggle)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from augint_tools.dashboard.app import (
    _PULSE_PHASES,
    _PULSE_TICK_SECONDS,
    FLASH_WINDOW_SECONDS,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_pulse_tick_seconds_value() -> None:
    assert _PULSE_TICK_SECONDS == pytest.approx(0.4)


def test_pulse_phases_value() -> None:
    assert _PULSE_PHASES == 4


def test_pulse_window_unchanged() -> None:
    """FLASH_WINDOW_SECONDS must remain 12-hour default."""
    assert FLASH_WINDOW_SECONDS == 12 * 60 * 60


# ---------------------------------------------------------------------------
# Phase cycling
# ---------------------------------------------------------------------------


def test_pulse_phase_cycles_full_range() -> None:
    """Phase must cycle through 0, 1, 2, 3 and wrap back to 0."""
    phase = 0
    seen = []
    for _ in range(_PULSE_PHASES * 2):
        seen.append(phase)
        phase = (phase + 1) % _PULSE_PHASES
    assert seen[:_PULSE_PHASES] == [0, 1, 2, 3]
    # Wraps back to the same sequence.
    assert seen[_PULSE_PHASES:] == seen[:_PULSE_PHASES]


def test_pulse_phase_never_exceeds_bound() -> None:
    phase = 0
    for _ in range(100):
        phase = (phase + 1) % _PULSE_PHASES
        assert 0 <= phase < _PULSE_PHASES


# ---------------------------------------------------------------------------
# DashboardApp._tick_flash advances _pulse_phase
# ---------------------------------------------------------------------------


def _make_app() -> object:
    """Return a DashboardApp with minimal mocking to avoid TUI bootstrap."""
    from augint_tools.dashboard.app import DashboardApp

    with (
        patch.object(DashboardApp, "on_mount", lambda self: None),
        patch("augint_tools.dashboard.app.DashboardApp.__init__", lambda self, **kw: None),
    ):
        app = object.__new__(DashboardApp)

    app._pulse_phase = 0  # type: ignore[attr-defined]
    app._flash_enabled = True  # type: ignore[attr-defined]
    app._main = None  # type: ignore[attr-defined]
    return app


def test_tick_flash_increments_phase() -> None:

    app = _make_app()
    app._tick_flash()  # type: ignore[attr-defined]
    assert app._pulse_phase == 1  # type: ignore[attr-defined]


def test_tick_flash_wraps_after_full_cycle() -> None:

    app = _make_app()
    for _ in range(_PULSE_PHASES):
        app._tick_flash()  # type: ignore[attr-defined]
    assert app._pulse_phase == 0  # type: ignore[attr-defined]


def test_tick_flash_calls_apply_pulse_phase_when_main_set() -> None:

    app = _make_app()
    mock_main = MagicMock()
    app._main = mock_main  # type: ignore[attr-defined]

    app._tick_flash()  # type: ignore[attr-defined]

    mock_main.apply_pulse_phase.assert_called_once_with(1, window_seconds=FLASH_WINDOW_SECONDS)


def test_tick_flash_passes_zero_when_flash_disabled() -> None:

    app = _make_app()
    app._flash_enabled = False  # type: ignore[attr-defined]
    mock_main = MagicMock()
    app._main = mock_main  # type: ignore[attr-defined]

    app._tick_flash()  # type: ignore[attr-defined]

    # Phase arg must be 0 regardless of internal _pulse_phase value.
    mock_main.apply_pulse_phase.assert_called_once_with(0, window_seconds=FLASH_WINDOW_SECONDS)


# ---------------------------------------------------------------------------
# RepoCard.apply_pulse_phase
# ---------------------------------------------------------------------------


def _make_card() -> object:
    """Return a bare RepoCard-like object with the pulse method patched in."""
    from augint_tools.dashboard.widgets.repo_card import RepoCard

    with patch.object(RepoCard, "__init__", lambda self, *a, **kw: None):
        card = object.__new__(RepoCard)

    # Provide just enough state for apply_pulse_phase / _is_recently_degraded.
    card._is_recently_degraded = MagicMock(return_value=False)  # type: ignore[attr-defined]
    card.remove_class = MagicMock()  # type: ignore[attr-defined]
    card.add_class = MagicMock()  # type: ignore[attr-defined]
    return card


def test_card_apply_pulse_phase_removes_all_on_phase_zero() -> None:

    card = _make_card()
    card._is_recently_degraded.return_value = True  # type: ignore[attr-defined]
    card.apply_pulse_phase(0, window_seconds=3600)  # type: ignore[attr-defined]

    for i in range(1, 4):
        card.remove_class.assert_any_call(f"card--pulse-{i}")  # type: ignore[attr-defined]
    card.add_class.assert_not_called()  # type: ignore[attr-defined]


def test_card_apply_pulse_phase_adds_correct_class_for_degraded() -> None:
    card = _make_card()
    card._is_recently_degraded.return_value = True  # type: ignore[attr-defined]

    card.apply_pulse_phase(2, window_seconds=3600)  # type: ignore[attr-defined]

    card.add_class.assert_called_once_with("card--pulse-2")  # type: ignore[attr-defined]


def test_card_apply_pulse_phase_no_class_when_not_degraded() -> None:
    card = _make_card()
    card._is_recently_degraded.return_value = False  # type: ignore[attr-defined]

    card.apply_pulse_phase(3, window_seconds=3600)  # type: ignore[attr-defined]

    card.add_class.assert_not_called()  # type: ignore[attr-defined]


def test_card_apply_pulse_phase_removes_stale_classes_before_adding() -> None:
    """All three pulse classes are removed before the new one is applied."""
    card = _make_card()
    card._is_recently_degraded.return_value = True  # type: ignore[attr-defined]

    card.apply_pulse_phase(1, window_seconds=3600)  # type: ignore[attr-defined]

    remove_calls = [c.args[0] for c in card.remove_class.call_args_list]  # type: ignore[attr-defined]
    assert set(remove_calls) == {"card--pulse-1", "card--pulse-2", "card--pulse-3"}
    card.add_class.assert_called_once_with("card--pulse-1")  # type: ignore[attr-defined]
