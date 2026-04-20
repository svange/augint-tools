"""Dashboard user preferences -- persist UI settings across sessions.

Preferences are stored as a JSON file alongside the existing health cache
so the dashboard restores the user's last theme, layout, sort, filters,
panel width, and blink setting on next launch.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from loguru import logger

from ._data import CACHE_DIR

PREFS_FILE = CACHE_DIR / "dashboard_prefs.json"


@dataclass
class DashboardPrefs:
    """Serialisable snapshot of user-chosen dashboard settings."""

    theme_name: str = "default"
    layout_name: str = "packed"
    sort_mode: str = "health"
    active_filters: list[str] = field(default_factory=list)
    panel_width: int = 38
    flash_enabled: bool = True
    disabled_repos: list[str] = field(default_factory=list)
    disabled_orgs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> DashboardPrefs:
        allowed = {f.name for f in __import__("dataclasses").fields(cls)}
        filtered = {k: v for k, v in data.items() if k in allowed}
        # Migrate legacy "no-workspace" -> "non-workspace".
        filters = filtered.get("active_filters")
        if isinstance(filters, list) and "no-workspace" in filters:
            filtered["active_filters"] = [
                "non-workspace" if f == "no-workspace" else f for f in filters
            ]
        return cls(**filtered)


def load_prefs() -> DashboardPrefs:
    """Load saved preferences from disk.  Returns defaults on any error."""
    if not PREFS_FILE.exists():
        return DashboardPrefs()
    try:
        data = json.loads(PREFS_FILE.read_text())
        return DashboardPrefs.from_dict(data)
    except (json.JSONDecodeError, TypeError, KeyError) as exc:
        logger.debug(f"prefs load failed, using defaults: {exc}")
        return DashboardPrefs()


def save_prefs(prefs: DashboardPrefs) -> None:
    """Persist preferences to disk."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        PREFS_FILE.write_text(json.dumps(prefs.to_dict(), indent=2))
    except OSError as exc:
        logger.debug(f"prefs save failed: {exc}")
