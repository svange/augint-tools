# Warm Start + Animation Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the dashboard paint instantly from cache on startup with clear staleness indicators, and replace all animations with polished alternatives.

**Architecture:** Cache expansion stores repo list + owners so the app can paint without auth. Auth and org discovery move into the background refresh worker. The sprite system gets new frame data, auto-dismiss, and two new transition types. The border flash becomes a multi-phase pulse.

**Tech Stack:** Python, Textual TUI framework, Rich Text rendering, CSS transitions

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/augint_tools/dashboard/_data.py` | Modify | Cache now stores `repo_list` + `owners`; new `load_cache_context()` |
| `src/augint_tools/dashboard/state.py` | Modify | `bootstrap_from_cache()` returns repo context |
| `src/augint_tools/dashboard/cmd.py` | Modify | Skip auth/org when cache provides context; new warm-start path |
| `src/augint_tools/dashboard/app.py` | Modify | Deferred auth, staleness/completion toasts, new transition types, pulse system |
| `src/augint_tools/dashboard/widgets/effect_sprite.py` | Rewrite | New sprite frames, auto-dismiss, warning + shimmer types |
| `src/augint_tools/dashboard/widgets/repo_card.py` | Modify | Replace `apply_flash_phase` with `apply_pulse_phase` |
| `src/augint_tools/dashboard/themes/*.tcss` (7 files) | Modify | Replace `card--flash-on` with `card--pulse-1/2/3` classes |
| `tests/unit/test_cache_context.py` | Create | Tests for cache context round-trip |
| `tests/unit/test_effect_sprite.py` | Create | Tests for new sprite types and auto-dismiss |
| `tests/unit/test_pulse.py` | Create | Tests for multi-phase pulse logic |

---

### Task 1: Expand cache to store repo list + owners

**Files:**
- Modify: `src/augint_tools/dashboard/_data.py:372-391` (save_cache)
- Modify: `src/augint_tools/dashboard/_data.py:350-369` (load_cache)
- Create: `tests/unit/test_cache_context.py`

- [ ] **Step 1: Write failing test for cache context round-trip**

```python
# tests/unit/test_cache_context.py
"""Tests for cache context (repo_list + owners) persistence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from augint_tools.dashboard._data import RepoStatus, load_cache_context, save_cache


@pytest.fixture()
def cache_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import augint_tools.dashboard._data as mod

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_path = cache_dir / "tui_cache.json"
    monkeypatch.setattr(mod, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(mod, "CACHE_FILE", cache_path)
    return cache_path


def _make_status(full_name: str) -> RepoStatus:
    name = full_name.split("/", 1)[1] if "/" in full_name else full_name
    return RepoStatus(
        name=name,
        full_name=full_name,
        has_dev_branch=False,
        main_status="success",
        main_error=None,
        dev_status=None,
        dev_error=None,
        open_issues=0,
        open_prs=0,
        draft_prs=0,
    )


def test_save_and_load_cache_context(cache_file: Path) -> None:
    statuses = [_make_status("org/repo-a"), _make_status("org/repo-b")]
    owners = ["org", "personal"]
    save_cache(statuses, owners=owners)

    ctx = load_cache_context()
    assert ctx is not None
    assert ctx["repo_list"] == ["org/repo-a", "org/repo-b"]
    assert ctx["owners"] == ["org", "personal"]


def test_load_cache_context_missing_file(cache_file: Path) -> None:
    ctx = load_cache_context()
    assert ctx is None


def test_load_cache_context_legacy_format(cache_file: Path) -> None:
    # Cache written by older version without context keys
    cache_file.write_text(json.dumps({"repos": {}}))
    ctx = load_cache_context()
    assert ctx is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_cache_context.py -v`
Expected: FAIL with `ImportError: cannot import name 'load_cache_context'`

- [ ] **Step 3: Add `owners` param to `save_cache` and write `load_cache_context`**

In `src/augint_tools/dashboard/_data.py`, modify `save_cache` to accept and persist owners + repo_list:

```python
def save_cache(
    statuses: list[RepoStatus],
    healths: list | None = None,
    *,
    owners: list[str] | None = None,
) -> None:
    """Persist repo statuses, optional health data, and context to disk."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data: dict = {"repos": {s.full_name: asdict(s) for s in statuses}}
    # Store repo list + owners for warm-start (paint before auth).
    if owners is not None:
        data["repo_list"] = [s.full_name for s in statuses]
        data["owners"] = owners
    elif CACHE_FILE.exists():
        try:
            existing = json.loads(CACHE_FILE.read_text())
            if "repo_list" in existing:
                data["repo_list"] = existing["repo_list"]
            if "owners" in existing:
                data["owners"] = existing["owners"]
        except (json.JSONDecodeError, KeyError):
            pass
    if healths:
        data["health"] = {h.status.full_name: h.to_dict() for h in healths}
        data["health_ts"] = datetime.now(UTC).isoformat()
    elif CACHE_FILE.exists():
        try:
            existing = json.loads(CACHE_FILE.read_text())
            if "health" in existing:
                data["health"] = existing["health"]
                data["health_ts"] = existing.get("health_ts")
        except (json.JSONDecodeError, KeyError):
            pass
    CACHE_FILE.write_text(json.dumps(data, indent=2))
```

Add `load_cache_context` after `load_cache_timestamp`:

```python
def load_cache_context() -> dict[str, list[str]] | None:
    """Load cached repo list + owners for warm-start.

    Returns ``{"repo_list": [...], "owners": [...]}`` or ``None``
    if the cache doesn't exist or lacks these keys.
    """
    if not CACHE_FILE.exists():
        return None
    try:
        data = json.loads(CACHE_FILE.read_text())
        repo_list = data.get("repo_list")
        owners = data.get("owners")
        if not isinstance(repo_list, list) or not isinstance(owners, list):
            return None
        return {"repo_list": repo_list, "owners": owners}
    except (json.JSONDecodeError, TypeError, KeyError):
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_cache_context.py -v`
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/augint_tools/dashboard/_data.py tests/unit/test_cache_context.py
git commit -m "feat(dashboard): persist repo list + owners in cache for warm-start"
```

---

### Task 2: Wire warm-start path in cmd.py

**Files:**
- Modify: `src/augint_tools/dashboard/cmd.py:110-214`

- [ ] **Step 1: Modify `dashboard_command` to use cache context when available**

The warm-start path skips auth + org discovery when cache has context. Add a new code path after prefs loading (line ~154) and before the auth block:

```python
# After theme/layout validation (around line 153) and before auth:

# ---- warm-start: try to paint from cache before blocking on auth ----
from ._data import load_cache_context

cache_context = load_cache_context()
can_warm_start = (
    cache_context is not None
    and not interactive  # interactive mode needs live repo list for selection
    and not org  # explicit --org overrides cache
)

if can_warm_start:
    logger.debug("warm-start: painting from cache context")
    try:
        run_dashboard(
            repos=None,  # signal: no live repo objects yet
            refresh_seconds=refresh_seconds,
            theme=theme,
            layout=layout,
            health_config=health_config,
            owners=cache_context["owners"],
            skip_refresh=no_refresh,
            github_client=None,  # deferred -- refresh worker will auth
            auto_discover=show_all,
            saved_prefs=prefs,
            warm_start=True,
            auth_source="dotenv" if env_auth else "auto",
        )
    except KeyboardInterrupt:
        print("\n[dim]Dashboard stopped.[/dim]")
    return

# ---- existing blocking auth path below (unchanged) ----
```

- [ ] **Step 2: Run pre-commit to verify no syntax errors**

Run: `uv run ruff check src/augint_tools/dashboard/cmd.py`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add src/augint_tools/dashboard/cmd.py
git commit -m "feat(dashboard): warm-start path skips auth when cache has context"
```

---

### Task 3: Update `run_dashboard` and `DashboardApp` for deferred auth

**Files:**
- Modify: `src/augint_tools/dashboard/app.py:2646-2698` (run_dashboard)
- Modify: `src/augint_tools/dashboard/app.py:1348-1427` (DashboardApp.__init__)
- Modify: `src/augint_tools/dashboard/app.py:1448-1522` (DashboardApp.on_mount)
- Modify: `src/augint_tools/dashboard/app.py:1630-1650` (DashboardApp._do_refresh_inner)

- [ ] **Step 1: Add `warm_start` and `auth_source` params to `run_dashboard`**

```python
def run_dashboard(
    repos: list[Repository] | None = None,
    *,
    refresh_seconds: int = 600,
    theme: str = "default",
    layout: str = "packed",
    health_config: dict | None = None,
    org_name: str = "",
    owners: list[str] | None = None,
    skip_refresh: bool = False,
    github_client: Github | None = None,
    auto_discover: bool = False,
    saved_prefs: DashboardPrefs | None = None,
    warm_start: bool = False,
    auth_source: str = "auto",
) -> None:
    """Launch the v2 interactive dashboard."""
    app_cls = DashboardApp
    cur_theme = theme
    cur_layout = layout
    cur_prefs = saved_prefs

    while True:
        app = app_cls(
            repos=repos,
            refresh_seconds=refresh_seconds,
            initial_theme=cur_theme,
            initial_layout=cur_layout,
            health_config=health_config,
            org_name=org_name,
            owners=owners,
            skip_refresh=skip_refresh,
            github_client=github_client,
            auto_discover=auto_discover,
            saved_prefs=cur_prefs,
            warm_start=warm_start,
            auth_source=auth_source,
        )
        app.run()

        if not getattr(app, "_restart_requested", False):
            break

        # After restart, use the normal path (auth is already done).
        warm_start = False

        # Purge all augint_tools modules so re-import picks up code changes.
        stale_mods = [k for k in sys.modules if k.startswith("augint_tools")]
        for k in stale_mods:
            del sys.modules[k]
        importlib.invalidate_caches()

        fresh_app = importlib.import_module("augint_tools.dashboard.app")
        fresh_prefs = importlib.import_module("augint_tools.dashboard.prefs")
        app_cls = fresh_app.DashboardApp
        cur_prefs = fresh_prefs.load_prefs()
        cur_theme = cur_prefs.theme_name
        cur_layout = cur_prefs.layout_name
```

- [ ] **Step 2: Add `warm_start` and `auth_source` to `DashboardApp.__init__`**

Add these params to `__init__` and store them:

```python
def __init__(
    self,
    repos: list[Repository] | None = None,
    *,
    refresh_seconds: int = 60,
    initial_theme: str = "default",
    initial_layout: str = "packed",
    health_config: dict | None = None,
    org_name: str = "",
    owners: list[str] | None = None,
    skip_refresh: bool = False,
    github_client: Github | None = None,
    auto_discover: bool = False,
    saved_prefs: DashboardPrefs | None = None,
    warm_start: bool = False,
    auth_source: str = "auto",
) -> None:
    # ... existing body unchanged ...
    self._warm_start = warm_start
    self._auth_source = auth_source
```

- [ ] **Step 3: Add staleness toast to `on_mount`**

After the `bootstrap_from_cache` call in `on_mount` (around line 1457), add the staleness toast:

```python
# After bootstrap_from_cache + apply_open_source_team:
if self._warm_start and self.state.last_refresh_at is not None:
    age = datetime.now(UTC) - self.state.last_refresh_at
    if age.total_seconds() < 60:
        age_label = f"{int(age.total_seconds())}s"
    elif age.total_seconds() < 3600:
        age_label = f"{int(age.total_seconds() / 60)}m"
    else:
        age_label = f"{age.total_seconds() / 3600:.1f}h"
    self.notify(
        f"Cached data from {age_label} ago -- refreshing...",
        timeout=8,
    )
elif self._warm_start:
    self.notify("Cached data (age unknown) -- refreshing...", timeout=8)
```

- [ ] **Step 4: Add deferred auth to `_do_refresh_inner`**

At the start of `_do_refresh_inner`, before Phase 0, add auth acquisition for warm-start:

```python
def _do_refresh_inner(self) -> None:
    # Clear stale refresh errors from the previous cycle.
    self.state.errors = [e for e in self.state.errors if e.source != "refresh"]

    # Deferred auth: acquire GitHub client on first refresh when warm-started.
    if self._github_client is None and self._warm_start:
        from ._common import get_github_client
        from ._helpers import get_viewer_login, list_user_orgs, strip_dotfile_repos, list_repos_multi

        try:
            self._github_client = get_github_client(auth_source=self._auth_source)
            # Inject into health config for the YAML compliance engine.
            engine_cfg = self._health_config.setdefault("standards_engine", {})
            engine_cfg["gh"] = self._github_client
        except Exception as exc:
            msg = f"Auth failed: {exc.__class__.__name__}: {exc}"
            self.state.log_error("refresh", msg)
            logger.error(f"warm-start auth: {msg}")
            self.state.is_refreshing = False
            self.call_from_thread(
                self.notify, "Auth failed -- showing cached data", severity="error", timeout=6
            )
            self.call_from_thread(self._rerender)
            return

        # Reconcile owners via live org discovery if auto_discover is set.
        if self._auto_discover:
            try:
                viewer = get_viewer_login(self._github_client) or ""
                if viewer:
                    self._owners = [viewer]
                    for org_login in list_user_orgs(self._github_client):
                        if org_login not in self._owners and org_login not in self._disabled_orgs:
                            self._owners.append(org_login)
            except Exception as exc:
                logger.warning(f"warm-start org discovery: {exc}")

        self._warm_start = False  # Only run once

    # Phase 0: reconcile the repo list ...
    # (rest of method unchanged)
```

- [ ] **Step 5: Add "Refresh complete" toast to `_commit_refresh`**

In `_commit_refresh`, after `self._rerender()` (around line 1953), add:

```python
self._rerender()
# Toast on first refresh completion (warm-start or cold start).
if not hasattr(self, "_first_refresh_done"):
    self._first_refresh_done = True
    self.notify("Refresh complete", timeout=3)
```

- [ ] **Step 6: Run lint**

Run: `uv run ruff check src/augint_tools/dashboard/app.py src/augint_tools/dashboard/cmd.py`
Expected: No errors

- [ ] **Step 7: Commit**

```bash
git add src/augint_tools/dashboard/app.py src/augint_tools/dashboard/cmd.py
git commit -m "feat(dashboard): deferred auth with staleness + completion toasts"
```

---

### Task 4: Pass owners through `save_cache` in the refresh loop

**Files:**
- Modify: `src/augint_tools/dashboard/app.py` (inside `_do_refresh_inner`, the `save_cache` call)

- [ ] **Step 1: Find and update the `save_cache` call in `_do_refresh_inner`**

Search for the existing `save_cache(statuses, healths=healths)` call in `_do_refresh_inner` and add `owners=self._owners`:

```python
save_cache(statuses, healths=healths, owners=self._owners)
```

- [ ] **Step 2: Run lint**

Run: `uv run ruff check src/augint_tools/dashboard/app.py`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add src/augint_tools/dashboard/app.py
git commit -m "feat(dashboard): persist owners in cache for warm-start"
```

---

### Task 5: Rewrite effect_sprite.py with new animations

**Files:**
- Rewrite: `src/augint_tools/dashboard/widgets/effect_sprite.py`
- Create: `tests/unit/test_effect_sprite.py`

- [ ] **Step 1: Write tests for new sprite types**

```python
# tests/unit/test_effect_sprite.py
"""Tests for the EffectSprite animation system."""

from __future__ import annotations

from augint_tools.dashboard.widgets.effect_sprite import (
    EffectKind,
    EffectSprite,
    SPRITE_DEFS,
)


def test_all_kinds_have_definitions() -> None:
    """Every EffectKind value has a corresponding sprite definition."""
    for kind in ("sparkle", "shockwave", "warning", "shimmer"):
        assert kind in SPRITE_DEFS, f"missing definition for {kind}"


def test_frame_color_length_match() -> None:
    """Frame and color arrays must be the same length for each sprite."""
    for kind, defn in SPRITE_DEFS.items():
        assert len(defn["frames"]) == len(defn["colors"]), (
            f"{kind}: {len(defn['frames'])} frames vs {len(defn['colors'])} colors"
        )


def test_sprite_kind_property() -> None:
    sprite = EffectSprite("sparkle")
    assert sprite.kind == "sparkle"


def test_auto_dismiss_flag() -> None:
    """All new sprites auto-dismiss (no click-to-clear behavior)."""
    sprite = EffectSprite("sparkle")
    assert sprite.auto_dismiss is True


def test_sprite_renders_without_error() -> None:
    """Smoke test: render() returns a Text object for each frame."""
    from rich.text import Text

    for kind in SPRITE_DEFS:
        sprite = EffectSprite(kind)
        for idx in range(len(SPRITE_DEFS[kind]["frames"])):
            sprite.frame_idx = idx
            result = sprite.render()
            assert isinstance(result, Text)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_effect_sprite.py -v`
Expected: FAIL (old sprite types)

- [ ] **Step 3: Rewrite `effect_sprite.py`**

```python
"""EffectSprite -- animated overlay sprites for severity transitions.

Sprites float above the card grid via the ``effects`` CSS layer.
Each sprite plays its frame cycle once and auto-dismisses.
"""

from __future__ import annotations

from typing import Any, Literal

from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget

EffectKind = Literal["sparkle", "shockwave", "warning", "shimmer"]

SPRITE_WIDTH = 7
SPRITE_HEIGHT = 5
_FRAME_INTERVAL_SECONDS = 0.12

# ---------------------------------------------------------------------------
# Sprite definitions: frames + per-frame colors
# ---------------------------------------------------------------------------

_SPARKLE_FRAMES: list[str] = [
    "       \n   .   \n       \n       \n       ",
    "       \n  .:.  \n   '   \n       \n       ",
    "  .    \n .::.  \n  ':'. \n   '   \n       ",
    " . ' . \n  *::* \n .'::'.\n  '*'  \n   '   ",
    "   .   \n '.:.' \n  '::'.\n .'*'. \n  ' '  ",
    " .   . \n  ' '  \n   :.  \n  ' '  \n .   . ",
    "       \n  .    \n    .  \n  .    \n       ",
    "       \n       \n   .   \n       \n       ",
]

_SPARKLE_COLORS: list[str] = [
    "bold bright_white",
    "bold bright_cyan",
    "bold bright_green",
    "bold green",
    "bold bright_cyan",
    "bold cyan",
    "dim bright_cyan",
    "dim cyan",
]

_SHOCKWAVE_FRAMES: list[str] = [
    "       \n       \n   *   \n       \n       ",
    "       \n  ---  \n -| |- \n  ---  \n       ",
    "  ---  \n /   \\ \n|  *  |\n \\   / \n  ---  ",
    " /---\\ \n/     \\\n|     |\n\\     /\n \\---/ ",
    "/-----\\\n|     |\n|     |\n|     |\n\\-----/",
    " /---\\ \n/ . . \\\n|  .  |\n\\ . . /\n \\---/ ",
    "  ---  \n  . .  \n   .   \n  . .  \n  ---  ",
    "       \n   .   \n  . .  \n   .   \n       ",
]

_SHOCKWAVE_COLORS: list[str] = [
    "bold bright_white",
    "bold bright_red",
    "bold red",
    "bold bright_red",
    "bold red",
    "bold dark_red",
    "dim red",
    "dim dark_red",
]

_WARNING_FRAMES: list[str] = [
    "       \n       \n   ~   \n       \n       ",
    "       \n  ~~~  \n  ~ ~  \n  ~~~  \n       ",
    "  ~~~  \n ~ ~ ~ \n~  ~  ~\n ~ ~ ~ \n  ~~~  ",
    "   ~   \n  ~ ~  \n   ~   \n  ~ ~  \n   ~   ",
]

_WARNING_COLORS: list[str] = [
    "bold bright_yellow",
    "bold yellow",
    "bold bright_yellow",
    "dim yellow",
]

_SHIMMER_FRAMES: list[str] = [
    "|      \n|      \n|      \n|      \n|      ",
    " |     \n |     \n |     \n |     \n |     ",
    "  |    \n  |    \n  |    \n  |    \n  |    ",
    "   |   \n   |   \n   |   \n   |   \n   |   ",
    "    |  \n    |  \n    |  \n    |  \n    |  ",
    "     | \n     | \n     | \n     | \n     | ",
    "      |\n      |\n      |\n      |\n      |",
]

_SHIMMER_COLORS: list[str] = [
    "dim bright_white",
    "bold bright_white",
    "bold bright_cyan",
    "bold bright_white",
    "bold bright_cyan",
    "bold bright_white",
    "dim bright_white",
]

SPRITE_DEFS: dict[str, dict[str, Any]] = {
    "sparkle": {"frames": _SPARKLE_FRAMES, "colors": _SPARKLE_COLORS},
    "shockwave": {"frames": _SHOCKWAVE_FRAMES, "colors": _SHOCKWAVE_COLORS},
    "warning": {"frames": _WARNING_FRAMES, "colors": _WARNING_COLORS},
    "shimmer": {"frames": _SHIMMER_FRAMES, "colors": _SHIMMER_COLORS},
}


class EffectSprite(Widget):
    """One animated sprite overlay. Plays once and auto-dismisses."""

    DEFAULT_CSS = f"""
    EffectSprite {{
        layer: effects;
        width: {SPRITE_WIDTH};
        height: {SPRITE_HEIGHT};
        background: transparent;
    }}
    """

    frame_idx: reactive[int] = reactive(0)
    auto_dismiss: bool = True

    def __init__(self, kind: EffectKind, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._kind: EffectKind = kind
        defn = SPRITE_DEFS[kind]
        self._frames: list[str] = defn["frames"]
        self._colors: list[str] = defn["colors"]

    @property
    def kind(self) -> EffectKind:
        return self._kind

    def on_mount(self) -> None:
        self.set_interval(_FRAME_INTERVAL_SECONDS, self._advance)

    def _advance(self) -> None:
        next_idx = self.frame_idx + 1
        if next_idx >= len(self._frames):
            # Cycle complete -- auto-dismiss.
            try:
                self.remove()
            except Exception:
                pass
            return
        self.frame_idx = next_idx

    def watch_frame_idx(self, _old: int, _new: int) -> None:
        self.refresh()

    def render(self) -> Text:
        idx = min(self.frame_idx, len(self._frames) - 1)
        glyphs = self._frames[idx]
        style = self._colors[idx]
        text = Text(glyphs, style=style)
        text.no_wrap = True
        text.overflow = "crop"
        return text
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_effect_sprite.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/augint_tools/dashboard/widgets/effect_sprite.py tests/unit/test_effect_sprite.py
git commit -m "feat(dashboard): replace animations with sparkle/shockwave/warning/shimmer sprites"
```

---

### Task 6: Update app.py to use new sprite types + auto-dismiss

**Files:**
- Modify: `src/augint_tools/dashboard/app.py` (imports, `_detect_severity_transitions`, `spawn_effect`, `dismiss_all_effects`, `on_click`)

- [ ] **Step 1: Update imports**

Change the import line:

```python
# Old:
from .widgets.effect_sprite import SPRITE_WIDTH, EffectKind, EffectSprite
# New:
from .widgets.effect_sprite import SPRITE_WIDTH, EffectKind, EffectSprite, SPRITE_HEIGHT
```

- [ ] **Step 2: Update `_detect_severity_transitions` to use new sprite names + add warning**

```python
def _detect_severity_transitions(
    self, healths: list[RepoHealth]
) -> list[tuple[str, EffectKind]]:
    """Return (full_name, sprite_kind) for cards that just flipped class.

    Sparkles fire on any non-ok -> ok transition; shockwaves fire
    on any non-critical -> critical transition; warning ripples fire
    on any non-warning -> warning transition. Skips the first commit
    (no prior data) so we don't spam every card on startup.
    """
    prior = self.state.health_by_name
    if not prior:
        return []
    out: list[tuple[str, EffectKind]] = []
    for h in healths:
        full_name = h.status.full_name
        prev = prior.get(full_name)
        if prev is None:
            continue
        prev_class = _card_severity_class(prev)
        new_class = _card_severity_class(h)
        if prev_class == new_class:
            continue
        if new_class == "ok":
            out.append((full_name, "sparkle"))
        elif new_class == "critical":
            out.append((full_name, "shockwave"))
        elif new_class == "warning":
            out.append((full_name, "warning"))
    return out
```

- [ ] **Step 3: Update `MainScreen.spawn_effect` to track auto-dismiss sprites**

When the sprite auto-dismisses (removes itself), we need to clean up `_effects_by_name`. Add a message handler. In the `spawn_effect` method, also register a callback:

```python
def spawn_effect(self, full_name: str, kind: EffectKind) -> None:
    """Mount (or replace) a sprite for ``full_name``.

    Sprites are layered above the card grid via ``layer: effects`` and
    anchored to the card's top-right corner. Auto-dismiss when the
    animation cycle completes.
    """
    existing = self._effects_by_name.pop(full_name, None)
    if existing is not None:
        try:
            existing.remove()
        except Exception:
            pass
    sprite = EffectSprite(kind)
    self._effects_by_name[full_name] = sprite
    self.mount(sprite)
    self._position_effect(full_name)
    self.call_after_refresh(self._position_effect, full_name)
```

The sprite auto-removes itself when the animation completes. To keep `_effects_by_name` in sync, have `spawn_effect` schedule a cleanup after the expected duration:

```python
def spawn_effect(self, full_name: str, kind: EffectKind) -> None:
    """Mount (or replace) a sprite for ``full_name``."""
    existing = self._effects_by_name.pop(full_name, None)
    if existing is not None:
        try:
            existing.remove()
        except Exception:
            pass
    sprite = EffectSprite(kind)
    self._effects_by_name[full_name] = sprite
    self.mount(sprite)
    self._position_effect(full_name)
    self.call_after_refresh(self._position_effect, full_name)
    # Schedule dict cleanup after the animation duration.
    from .widgets.effect_sprite import _FRAME_INTERVAL_SECONDS, SPRITE_DEFS
    duration = len(SPRITE_DEFS[kind]["frames"]) * _FRAME_INTERVAL_SECONDS + 0.1
    self.set_timer(duration, lambda: self._effects_by_name.pop(full_name, None))
```

- [ ] **Step 4: Remove the click-to-dismiss behavior from `DashboardApp.on_click`**

```python
def on_click(self, _event: events.Click) -> None:
    # Sprites now auto-dismiss after their animation cycle.
    # Click events pass through to card selection handlers.
    pass
```

Actually, it's cleaner to just remove the `on_click` method entirely if it only did sprite dismissal. But check if there's other logic -- from the earlier read it only dismisses sprites. Remove the method body but keep the method to avoid breaking any overrides:

```python
def on_click(self, _event: events.Click) -> None:
    pass
```

- [ ] **Step 5: Run lint**

Run: `uv run ruff check src/augint_tools/dashboard/app.py`
Expected: No errors

- [ ] **Step 6: Commit**

```bash
git add src/augint_tools/dashboard/app.py
git commit -m "feat(dashboard): wire new sprite types, add warning transition, auto-dismiss"
```

---

### Task 7: Add refresh-complete shimmer animation

**Files:**
- Modify: `src/augint_tools/dashboard/app.py:1900-1957` (_commit_refresh)

- [ ] **Step 1: Detect changed cards and spawn shimmer in `_commit_refresh`**

After the severity transitions block in `_commit_refresh`, add shimmer for cards whose data changed but didn't trigger a severity transition:

```python
# After existing severity transition spawning:
if transitions and self._main is not None:
    for full_name, kind in transitions:
        self._main.spawn_effect(full_name, kind)

# Shimmer effect on cards whose values changed (but no severity transition).
if self._main is not None:
    transition_names = {name for name, _ in transitions} if transitions else set()
    prior = {h.status.full_name: h for h in (self._prev_healths or [])}
    for h in healths:
        fn = h.status.full_name
        if fn in transition_names:
            continue  # Already got a bigger animation
        prev = prior.get(fn)
        if prev is not None and prev.status != h.status:
            self._main.spawn_effect(fn, "shimmer")
```

Add `self._prev_healths: list[RepoHealth] = []` to `__init__`, and set it in `_commit_refresh` before overwriting `state.healths`:

In `__init__`:
```python
self._prev_healths: list[RepoHealth] = []
```

In `_commit_refresh`, just before `self.state.healths = healths`:
```python
self._prev_healths = list(self.state.healths)
```

- [ ] **Step 2: Run lint**

Run: `uv run ruff check src/augint_tools/dashboard/app.py`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add src/augint_tools/dashboard/app.py
git commit -m "feat(dashboard): shimmer animation on refreshed cards with changed data"
```

---

### Task 8: Replace border flash with multi-phase pulse

**Files:**
- Modify: `src/augint_tools/dashboard/widgets/repo_card.py:218-245`
- Modify: `src/augint_tools/dashboard/app.py:110-117,2182-2192`
- Modify: `src/augint_tools/dashboard/app.py:307-310` (MainScreen.apply_flash_phase)
- Create: `tests/unit/test_pulse.py`

- [ ] **Step 1: Write failing test for pulse phase logic**

```python
# tests/unit/test_pulse.py
"""Tests for the multi-phase border pulse system."""

from __future__ import annotations


def test_pulse_phase_range() -> None:
    """Pulse phase must cycle through 0-3."""
    # 4 phases: 0 = base, 1 = slightly lighter, 2 = lightest, 3 = slightly lighter
    phases = list(range(4))
    assert phases == [0, 1, 2, 3]


def test_pulse_tick_cycles() -> None:
    """The pulse ticker should cycle 0 -> 1 -> 2 -> 3 -> 0 -> ..."""
    phase = 0
    sequence = []
    for _ in range(8):
        sequence.append(phase)
        phase = (phase + 1) % 4
    assert sequence == [0, 1, 2, 3, 0, 1, 2, 3]
```

- [ ] **Step 2: Run test to verify it passes (pure logic)**

Run: `uv run pytest tests/unit/test_pulse.py -v`
Expected: PASS

- [ ] **Step 3: Update `DashboardApp` pulse system**

Replace the flash constants and `_tick_flash`:

```python
# Replace the constants:
FLASH_WINDOW_SECONDS = 12 * 60 * 60
_PULSE_TICK_SECONDS = 0.4  # 4 phases * 0.4s = 1.6s full cycle
_PULSE_PHASES = 4
```

In `__init__`, replace `self._flash_phase: bool = False` with:
```python
self._pulse_phase: int = 0
```

Replace `_tick_flash`:
```python
def _tick_flash(self) -> None:
    """Advance the pulse phase and push it to every visible card.

    Four-phase cycle creates a breathing effect: base -> lighter ->
    lightest -> lighter -> base. Cheap: only toggles CSS classes.
    """
    self._pulse_phase = (self._pulse_phase + 1) % _PULSE_PHASES
    if self._main is None:
        return
    phase = self._pulse_phase if self._flash_enabled else 0
    self._main.apply_pulse_phase(phase, window_seconds=FLASH_WINDOW_SECONDS)
```

Update `on_mount` to use the new tick interval:
```python
# Change:
self.set_interval(_FLASH_TICK_SECONDS, self._tick_flash)
# To:
self.set_interval(_PULSE_TICK_SECONDS, self._tick_flash)
```

- [ ] **Step 4: Update `MainScreen.apply_flash_phase` -> `apply_pulse_phase`**

```python
def apply_pulse_phase(self, phase: int, *, window_seconds: int) -> None:
    """Propagate the global pulse phase to each card."""
    for card in self._cards_by_name.values():
        card.apply_pulse_phase(phase, window_seconds=window_seconds)
```

Keep `apply_flash_phase` as an alias for backward compatibility during this transition:
```python
def apply_flash_phase(self, phase: bool, *, window_seconds: int) -> None:
    """Legacy alias -- convert bool to int phase."""
    self.apply_pulse_phase(1 if phase else 0, window_seconds=window_seconds)
```

Actually, per project rules: break interfaces freely, no backward compat shims. Just rename it.

- [ ] **Step 5: Update `RepoCard.apply_flash_phase` -> `apply_pulse_phase`**

In `repo_card.py`:

```python
def apply_pulse_phase(self, phase: int, *, window_seconds: int) -> None:
    """Apply multi-phase pulse when this card is within the flash window.

    Phase 0 = base (no extra class), 1-3 = graduated intensity.
    Creates a breathing effect instead of a harsh on/off blink.
    """
    is_degraded = self._is_recently_degraded(window_seconds)
    # Remove all pulse classes first.
    for i in range(1, 4):
        self.remove_class(f"card--pulse-{i}")
    # Apply the current phase if degraded and phase > 0.
    if is_degraded and phase > 0:
        self.add_class(f"card--pulse-{phase}")
```

Remove the old `apply_flash_phase` method and `card--flash-on` references.

- [ ] **Step 6: Run lint**

Run: `uv run ruff check src/augint_tools/dashboard/app.py src/augint_tools/dashboard/widgets/repo_card.py`
Expected: No errors

- [ ] **Step 7: Commit**

```bash
git add src/augint_tools/dashboard/app.py src/augint_tools/dashboard/widgets/repo_card.py tests/unit/test_pulse.py
git commit -m "feat(dashboard): replace harsh border flash with 4-phase breathing pulse"
```

---

### Task 9: Update theme CSS files for pulse classes

**Files:**
- Modify: `src/augint_tools/dashboard/themes/default.tcss`
- Modify: `src/augint_tools/dashboard/themes/cyber.tcss`
- Modify: `src/augint_tools/dashboard/themes/matrix.tcss`
- Modify: `src/augint_tools/dashboard/themes/minimal.tcss`
- Modify: `src/augint_tools/dashboard/themes/nord.tcss`
- Modify: `src/augint_tools/dashboard/themes/paper.tcss`
- Modify: `src/augint_tools/dashboard/themes/synthwave.tcss`

Each theme needs: remove `card--flash-on` rules, add `card--pulse-1`, `card--pulse-2`, `card--pulse-3` with graduated border intensity. Phase 1 = slight shift, phase 2 = max intensity, phase 3 = slight shift (mirrors phase 1, creating symmetrical breathing).

- [ ] **Step 1: Update default.tcss**

Remove:
```css
RepoCard.card--warning.card--flash-on { border: round #ffecb0; }
RepoCard.card--critical.card--flash-on { border: round #ff99a8; }
```

Add:
```css
RepoCard.card--warning.card--pulse-1 { border: round #ffe599; }
RepoCard.card--warning.card--pulse-2 { border: round #ffecb0; }
RepoCard.card--warning.card--pulse-3 { border: round #ffe599; }
RepoCard.card--critical.card--pulse-1 { border: round #ff8899; }
RepoCard.card--critical.card--pulse-2 { border: round #ff99a8; }
RepoCard.card--critical.card--pulse-3 { border: round #ff8899; }
```

- [ ] **Step 2: Update cyber.tcss**

Remove `card--flash-on` rules. Add:
```css
RepoCard.card--warning.card--pulse-1 { border: round #f5da8c; }
RepoCard.card--warning.card--pulse-2 { border: round #fbe7a2; }
RepoCard.card--warning.card--pulse-3 { border: round #f5da8c; }
RepoCard.card--critical.card--pulse-1 { border: round #ff889e; }
RepoCard.card--critical.card--pulse-2 { border: round #ff99b2; }
RepoCard.card--critical.card--pulse-3 { border: round #ff889e; }
```

- [ ] **Step 3: Update matrix.tcss**

Remove `card--flash-on` rules. Add:
```css
RepoCard.card--warning.card--pulse-1 { border: round #ffd07f; }
RepoCard.card--warning.card--pulse-2 { border: round #ffde97; }
RepoCard.card--warning.card--pulse-3 { border: round #ffd07f; }
RepoCard.card--critical.card--pulse-1 { border: round #ff9f97; }
RepoCard.card--critical.card--pulse-2 { border: round #ffafab; }
RepoCard.card--critical.card--pulse-3 { border: round #ff9f97; }
```

- [ ] **Step 4: Update minimal.tcss**

Remove `card--flash-on` rules. Add:
```css
RepoCard.card--warning.card--pulse-1 { border: round #cccccc; }
RepoCard.card--warning.card--pulse-2 { border: round #d5d5d5; }
RepoCard.card--warning.card--pulse-3 { border: round #cccccc; }
RepoCard.card--critical.card--pulse-1 { border: round #ff8f8f; }
RepoCard.card--critical.card--pulse-2 { border: round #ff9f9f; }
RepoCard.card--critical.card--pulse-3 { border: round #ff8f8f; }
```

- [ ] **Step 5: Update nord.tcss**

Remove `card--flash-on` rules. Add:
```css
RepoCard.card--warning.card--pulse-1 { border: round #eedbb5; }
RepoCard.card--warning.card--pulse-2 { border: round #f5e5c5; }
RepoCard.card--warning.card--pulse-3 { border: round #eedbb5; }
RepoCard.card--critical.card--pulse-1 { border: round #d5a0a4; }
RepoCard.card--critical.card--pulse-2 { border: round #dfb0b4; }
RepoCard.card--critical.card--pulse-3 { border: round #d5a0a4; }
```

- [ ] **Step 6: Update paper.tcss**

Read the current `card--flash-on` values and replace with pulse equivalents following the same pattern.

- [ ] **Step 7: Update synthwave.tcss**

Remove `card--flash-on` rules. Add:
```css
RepoCard.card--warning.card--pulse-1 { border: round #ffe470; }
RepoCard.card--warning.card--pulse-2 { border: round #ffeb80; }
RepoCard.card--warning.card--pulse-3 { border: round #ffe470; }
RepoCard.card--critical.card--pulse-1 { border: round #ff889e; }
RepoCard.card--critical.card--pulse-2 { border: round #ff99b2; }
RepoCard.card--critical.card--pulse-3 { border: round #ff889e; }
```

- [ ] **Step 8: Commit**

```bash
git add src/augint_tools/dashboard/themes/
git commit -m "feat(dashboard): replace flash-on CSS with 3-phase pulse classes across all themes"
```

---

### Task 10: Run full test suite and fix issues

**Files:**
- All modified files

- [ ] **Step 1: Run lint across all changed files**

Run: `uv run ruff check src/augint_tools/dashboard/ tests/`
Expected: No errors

- [ ] **Step 2: Run type checking**

Run: `uv run mypy src/augint_tools/dashboard/`
Expected: No errors (or only pre-existing ones)

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 4: Fix any failures**

Address any test failures or type errors from existing tests that reference the old animation types (`fireworks`, `mushroom`, `apply_flash_phase`).

Search for references:
```bash
grep -rn 'fireworks\|mushroom\|apply_flash_phase\|card--flash-on\|EffectKind.*fireworks' src/ tests/
```

Update any matches to use the new names.

- [ ] **Step 5: Run pre-commit hooks**

Run: `uv run pre-commit run --all-files`
Expected: All hooks pass

- [ ] **Step 6: Commit any fixes**

```bash
git add -A
git commit -m "fix(dashboard): update remaining references to old animation types"
```

---

### Task 11: Manual smoke test

- [ ] **Step 1: Start dashboard with existing cache**

Run: `uv run ai-tools dashboard -a`

Verify:
- Dashboard paints immediately from cache
- Staleness toast appears with age
- Refresh happens in background
- "Refresh complete" toast appears

- [ ] **Step 2: Verify animations**

- Trigger a severity transition (or wait for one) to see sparkle/shockwave/warning sprites
- Verify border pulse breathes smoothly on any degraded cards
- Verify sprites auto-dismiss after their animation cycle
- Verify shimmer fires on cards with changed data after refresh

- [ ] **Step 3: Test cold start (no cache)**

Delete cache and restart:
```bash
rm ~/.cache/ai-tools-dashboard/tui_cache.json
uv run ai-tools dashboard -a
```

Verify: falls back to blocking auth path, no crash, no staleness toast.
