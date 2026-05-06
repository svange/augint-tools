from __future__ import annotations

from types import SimpleNamespace

from augint_tools.dashboard.health import Severity
from augint_tools.dashboard.layouts import LayoutContext
from augint_tools.dashboard.layouts.dense import DenseLayout
from augint_tools.dashboard.layouts.list import ListLayout
from augint_tools.dashboard.layouts.severity import SeverityLayout, _card_severity_bucket
from augint_tools.dashboard.state import AppState


class _FakeCard:
    def __init__(self, health=None):
        self.health = health
        self.styles = SimpleNamespace(width=None, height=None)
        self.render_mode = None
        self.removed: list[tuple[str, ...]] = []
        self.added: list[str] = []

    def remove_class(self, *names: str) -> None:
        self.removed.append(names)

    def add_class(self, name: str) -> None:
        self.added.append(name)


class _FailingStyles:
    @property
    def grid_size_columns(self):  # pragma: no cover - not used
        return None

    @grid_size_columns.setter
    def grid_size_columns(self, _value: int) -> None:
        raise RuntimeError("style failure")


class _FakeContainer:
    def __init__(self, *, failing_styles: bool = False):
        self.styles = (
            _FailingStyles() if failing_styles else SimpleNamespace(grid_size_columns=None)
        )
        self.removed: list[tuple[str, ...]] = []
        self.added: list[str] = []
        self.headers = None
        self.cleared = False

    def remove_class(self, *names: str) -> None:
        self.removed.append(names)

    def add_class(self, name: str) -> None:
        self.added.append(name)

    def clear_group_headers(self) -> None:
        self.cleared = True

    def set_group_headers(self, order, headers, buckets, *, columns: int) -> None:
        self.headers = (order, headers, buckets, columns)


def test_dense_layout_apply_sets_dense_mode():
    layout = DenseLayout()
    container = _FakeContainer()
    cards = [_FakeCard(), _FakeCard()]
    ctx = LayoutContext(state=AppState(), available_width=80)
    layout.apply(container, cards, ctx)
    assert "layout--dense" in container.added
    assert container.cleared is True
    assert container.styles.grid_size_columns == 3
    assert all(card.render_mode == "dense" for card in cards)
    assert all("card--dense" in card.added for card in cards)


def test_dense_layout_handles_style_assignment_error():
    layout = DenseLayout()
    container = _FakeContainer(failing_styles=True)
    layout.apply(container, [_FakeCard()], LayoutContext(state=AppState(), available_width=0))
    assert "layout--dense" in container.added


def test_list_layout_apply_sets_list_mode():
    layout = ListLayout()
    container = _FakeContainer()
    cards = [_FakeCard()]
    layout.apply(container, cards, LayoutContext(state=AppState(), available_width=100))
    assert "layout--list" in container.added
    assert cards[0].styles.width == "100%"
    assert cards[0].render_mode == "list"
    assert "card--list" in cards[0].added


def test_card_severity_bucket():
    failing = SimpleNamespace(status=SimpleNamespace(main_status="failure", dev_status="success"))
    assert _card_severity_bucket(failing) == "critical"

    warning = SimpleNamespace(
        status=SimpleNamespace(main_status="success", dev_status="success"),
        worst_severity=Severity.MEDIUM,
    )
    assert _card_severity_bucket(warning) == "warning"

    healthy = SimpleNamespace(
        status=SimpleNamespace(main_status="success", dev_status="success"),
        worst_severity=Severity.OK,
    )
    assert _card_severity_bucket(healthy) == "ok"
    assert _card_severity_bucket(None) == "ok"


def test_severity_layout_groups_cards_and_sets_width():
    layout = SeverityLayout()
    container = _FakeContainer()
    state = AppState(panel_width=42)
    cards = [
        _FakeCard(
            SimpleNamespace(status=SimpleNamespace(main_status="failure", dev_status="success"))
        ),
        _FakeCard(
            SimpleNamespace(
                status=SimpleNamespace(main_status="success", dev_status="success"),
                worst_severity=Severity.HIGH,
            )
        ),
        _FakeCard(
            SimpleNamespace(
                status=SimpleNamespace(main_status="success", dev_status="success"),
                worst_severity=Severity.OK,
            )
        ),
    ]
    layout.apply(container, cards, LayoutContext(state=state, available_width=120))
    assert "layout--severity" in container.added
    assert container.styles.grid_size_columns == 2
    assert container.headers is not None
    assert all(card.styles.width == 42 for card in cards)
    assert all(card.render_mode == "packed" for card in cards)
