# Dashboard Warm Start + Animation Overhaul

## Problem

Dashboard startup has three UX issues:

1. **Slow time-to-first-paint.** Auth, org discovery, and repo list resolution all block before the app mounts. Cache exists on disk but isn't used until after these blocking calls.
2. **No staleness indicator.** When cached data paints, there's no way to tell it's stale. Users don't trust what they see.
3. **Jarring transitions.** When the live refresh completes, cards shift/jump. The existing fireworks (ASCII starburst) and mushroom cloud sprites are crude. The border flash is a harsh on/off strobe.

## Solution

Two changes shipped together:

### 1. Startup Flow Inversion

**Current order:** auth -> org discovery -> repo list -> mount app -> paint cache -> refresh

**New order:** mount app -> paint cache + staleness toast -> auth/org/refresh in background -> update display + completion toast

#### Cache expansion

`tui_cache.json` gains two new top-level keys:

```json
{
  "repos": { ... },
  "health": { ... },
  "health_ts": "...",
  "repo_list": ["owner/repo1", "owner/repo2"],
  "owners": ["owner1", "owner2"]
}
```

These are written alongside existing data in `save_cache()` and read back in `load_cache()`.

#### Startup sequence

1. `dashboard_command()` attempts to load cache. If cache exists with `repo_list`, it skips auth/org discovery and passes the cached repo list + owners directly to `DashboardApp`.
2. `DashboardApp.on_mount()` calls `bootstrap_from_cache()` as today, which now also has the repo list. `_rebuild_cards()` paints immediately.
3. A staleness toast fires: `"Cached data from {age} ago -- refreshing..."` computed from `health_ts`.
4. The first `_do_refresh_inner()` call performs auth, reconciles the cached repo list against the live org state (adds new repos, drops deleted ones), then proceeds with the normal GraphQL/REST fetch cycle.
5. When the first refresh commits to the main thread, a toast fires: `"Refresh complete"`.

If no cache exists (first launch), the current blocking flow runs as-is.

`--no-refresh` continues to skip the refresh entirely (offline/testing use case).

#### Auth deferral

Auth (`get_github_client()`) moves from `dashboard_command()` into the refresh worker. The worker stores the authenticated client on `DashboardApp` once obtained. If auth fails, the worker posts an error toast and the dashboard stays in cache-only mode.

### 2. Animation Overhaul

#### Sprite system changes (effect_sprite.py)

- Frame rate: 0.33s/frame -> 0.12s/frame (~8fps)
- All sprites auto-dismiss after completing their frame cycle (remove the "click to dismiss" behavior)
- Sprites still use `layer: effects` positioning at the card's top-right

#### Replaced animations

**OK transition (was: fireworks ASCII starburst)**
New: Rising sparkle cascade. 6-8 frames. Particles (braille/box-drawing characters) rise upward from the card with a fading trail. Colors cycle through greens and cyans with a white flash at peak. Final frames fade out.

**CRITICAL transition (was: mushroom cloud)**
New: Pulsing shockwave. 6-8 frames. Expanding concentric rings (unicode block elements) from the card. Colors pulse bright red -> red -> dark red. Contracts slightly before fade-out.

**Border flash (was: 0.6s on/off CSS class toggle)**
New: Smooth border pulse. Multi-phase cycle (4 phases) that fades border intensity up and down, creating a breathing effect instead of a strobe. Implemented via multiple CSS classes cycled in sequence rather than a single toggle.

#### New animations

**WARNING transition (any -> WARNING):** Brief amber ripple, 4 frames. Subtle attention-draw without alarm.

**Refresh-complete shimmer:** Changed cards get a brief highlight sweep left-to-right across the card border, 3-4 frames. Fires when `_commit_refresh()` detects value changes on a card.

## Files Changed

| File | Change |
|------|--------|
| `_data.py` | `save_cache()` writes `repo_list` + `owners`; `load_cache()` reads them back |
| `state.py` | `bootstrap_from_cache()` returns repo list + owners alongside statuses |
| `cmd.py` | Skip auth/org discovery when cache provides repo list; pass cache context to app |
| `app.py` | Reorder `on_mount()`: paint then refresh. Add staleness + completion toasts. Move auth into refresh worker. Update `_detect_severity_transitions()` for new animation types. Add refresh-complete shimmer trigger in `_commit_refresh()`. Replace flash toggle with multi-phase pulse cycle. |
| `effect_sprite.py` | Rewrite all sprite frame data, colors, and timing. Add warning ripple and shimmer sprite types. Auto-dismiss after cycle. |
| `repo_card.py` | Replace `apply_flash_phase(phase)` with `apply_pulse_phase(phase)` supporting 4 phases |
| `themes/*.tcss` | Replace `card--flash-on` class with `card--pulse-1` through `card--pulse-3` for graduated border intensity |

## Not in scope

Deferred to svange/augint-tools#103:
- Configurable animation packs (like themes/layouts)
- `--animations` flag for selecting animation style
- Third-party animation library support
