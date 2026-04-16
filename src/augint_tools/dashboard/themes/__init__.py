"""Theme registry for the v2 dashboard.

A theme is a ``.tcss`` file plus a small :class:`ThemeSpec` for values that
do not belong in CSS (severity-to-colour mapping, severity icons, etc.).
Adding a new theme is one file + one :func:`register_theme` call.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..health import Severity

_THEMES_DIR = Path(__file__).parent


@dataclass(frozen=True)
class ThemeSpec:
    """Non-CSS theme values.

    Severity colours here feed Rich-rendered in-widget text; the CSS handles
    borders, backgrounds, and textual chrome.
    """

    name: str
    css_path: Path
    severity_colors: dict[Severity, str]
    severity_icons: dict[Severity, str]
    status_pass: str
    status_fail: str
    status_running: str
    status_unknown: str


_THEMES: dict[str, ThemeSpec] = {}


def register_theme(spec: ThemeSpec) -> None:
    _THEMES[spec.name] = spec


def get_theme(name: str) -> ThemeSpec:
    try:
        return _THEMES[name]
    except KeyError as exc:
        raise KeyError(f"Unknown theme '{name}'. Available: {list_themes()}") from exc


def list_themes() -> list[str]:
    return list(_THEMES.keys())


# ---------------------------------------------------------------------------
# Built-in themes -- register at import so callers see a populated registry.
# ---------------------------------------------------------------------------


def _spec(
    name: str,
    css: str,
    *,
    critical: str,
    high: str,
    medium: str,
    low: str,
    ok: str,
    status_pass: str,
    status_fail: str,
    status_running: str,
    status_unknown: str,
    icons: dict[Severity, str] | None = None,
) -> ThemeSpec:
    return ThemeSpec(
        name=name,
        css_path=_THEMES_DIR / css,
        severity_colors={
            Severity.CRITICAL: critical,
            Severity.HIGH: high,
            Severity.MEDIUM: medium,
            Severity.LOW: low,
            Severity.OK: ok,
        },
        severity_icons=icons
        or {
            Severity.CRITICAL: "!",
            Severity.HIGH: "!",
            Severity.MEDIUM: "*",
            Severity.LOW: "-",
            Severity.OK: ".",
        },
        status_pass=status_pass,
        status_fail=status_fail,
        status_running=status_running,
        status_unknown=status_unknown,
    )


def _register_builtins() -> None:
    # paper -- default: calm, colour only on failures.
    register_theme(
        _spec(
            "paper",
            "paper.tcss",
            critical="red",
            high="dark_orange",
            medium="yellow",
            low="bright_black",
            ok="green",
            status_pass="green",
            status_fail="red",
            status_running="yellow",
            status_unknown="bright_black",
        )
    )
    register_theme(
        _spec(
            "nord",
            "nord.tcss",
            critical="#bf616a",
            high="#d08770",
            medium="#ebcb8b",
            low="#81a1c1",
            ok="#a3be8c",
            status_pass="#a3be8c",
            status_fail="#bf616a",
            status_running="#ebcb8b",
            status_unknown="#4c566a",
        )
    )
    register_theme(
        _spec(
            "default",
            "default.tcss",
            critical="bold red",
            high="bold #ff8800",
            medium="yellow",
            low="dim cyan",
            ok="green",
            status_pass="#72f1b8",
            status_fail="#ff3355",
            status_running="#ffd84d",
            status_unknown="#b8bcc8",
        )
    )
    register_theme(
        _spec(
            "minimal",
            "minimal.tcss",
            critical="red",
            high="bright_white",
            medium="bright_white",
            low="bright_black",
            ok="bright_black",
            status_pass="bright_white",
            status_fail="red",
            status_running="yellow",
            status_unknown="bright_black",
        )
    )
    register_theme(
        _spec(
            "cyber",
            "cyber.tcss",
            critical="#ff3366",
            high="#ff8800",
            medium="#f7d046",
            low="#3ac3ff",
            ok="#72f1b8",
            status_pass="#72f1b8",
            status_fail="#ff3366",
            status_running="#f7d046",
            status_unknown="#8791b0",
        )
    )
    register_theme(
        _spec(
            "matrix",
            "matrix.tcss",
            critical="#ff5f56",
            high="#ffbd2e",
            medium="#9df08f",
            low="#2dd062",
            ok="#00ff41",
            status_pass="#00ff41",
            status_fail="#ff5f56",
            status_running="#ffbd2e",
            status_unknown="#337d3a",
        )
    )
    register_theme(
        _spec(
            "synthwave",
            "synthwave.tcss",
            critical="#ff3366",
            high="#ff7f50",
            medium="#ffd700",
            low="#8affff",
            ok="#a6ff9c",
            status_pass="#a6ff9c",
            status_fail="#ff3366",
            status_running="#ffd700",
            status_unknown="#8fb2d6",
        )
    )


_register_builtins()

__all__ = [
    "ThemeSpec",
    "get_theme",
    "list_themes",
    "register_theme",
]
