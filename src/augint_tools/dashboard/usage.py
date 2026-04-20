"""Usage data providers for the panel dashboard sidebar.

Providers prefer SSO / local session data, and optionally use API keys where
SSO is not available (OpenAI).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

# Approximate Claude subscription caps. Anthropic publishes these in messages
# per rolling window and they change periodically -- keep them here so they're
# easy to adjust, and fall back to "no bar, raw count only" when the tier is
# unknown.
#
# Sources (checked 2026-04; re-verify when Anthropic updates the help centre):
#   https://support.anthropic.com/en/articles/8324991-about-claude-pro-and-team-plans
#   https://support.anthropic.com/en/articles/11145838-using-claude-code-with-your-subscription
#
# The 5-hour window is the active-session cap (resets 5 hours after the first
# message in a session). The 7-day window is the rolling weekly cap.
_CLAUDE_TIER_5H_LIMITS: dict[str, int] = {
    "default_claude_pro": 45,
    "default_claude_max_5x": 225,
    "default_claude_max_20x": 900,
    "default_claude_team": 225,
    "default_claude_enterprise": 900,
}

_CLAUDE_TIER_WEEKLY_LIMITS: dict[str, int] = {
    "default_claude_pro": 1500,
    "default_claude_max_5x": 7500,
    "default_claude_max_20x": 30000,
    "default_claude_team": 10000,
    "default_claude_enterprise": 50000,
}

# Legacy default weekly window used when a caller doesn't specify one.
_DEFAULT_WINDOW_DAYS = 7
_FIVE_HOUR_SECONDS = 5 * 3600


@dataclass(frozen=True)
class UsageStats:
    """Usage statistics for a single provider within a time window."""

    provider: str
    display_name: str
    window_days: int = 7
    messages: int = 0
    sessions: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    limit: int | None = None
    # Seconds until the window reset (rolling: time until oldest message ages out).
    time_remaining_seconds: int | None = None
    # Seconds for the full window (e.g. 7 * 86400 for weekly).
    window_total_seconds: int | None = None
    # Subscription / plan tier label for display.
    tier: str | None = None
    status: str = "ok"  # ok, warning, critical, unknown, unconfigured, unavailable, empty
    error: str | None = None
    # Free-form note shown under the progress bar (e.g. data source).
    note: str | None = None
    # Claude-specific: messages and limits in the rolling 5-hour session window.
    hour5_used: int | None = None
    hour5_limit: int | None = None
    # Claude-specific: messages and limits in the rolling 7-day window.
    # These mirror ``messages`` / ``limit`` but are named explicitly so the
    # widget can render both windows side by side.
    week7_used: int | None = None
    week7_limit: int | None = None

    @property
    def usage_fraction(self) -> float | None:
        """Return usage as a fraction of the limit, or None if no limit set."""
        if self.limit is None or self.limit <= 0:
            return None
        return min(1.0, self.messages / self.limit)

    @property
    def hour5_fraction(self) -> float | None:
        """5-hour window usage as fraction of limit, or None when unknown."""
        if self.hour5_used is None or self.hour5_limit is None or self.hour5_limit <= 0:
            return None
        return min(1.0, self.hour5_used / self.hour5_limit)

    @property
    def week7_fraction(self) -> float | None:
        """7-day window usage as fraction of limit, or None when unknown."""
        if self.week7_used is None or self.week7_limit is None or self.week7_limit <= 0:
            return None
        return min(1.0, self.week7_used / self.week7_limit)

    @property
    def time_elapsed_fraction(self) -> float | None:
        """Fraction of the time window that has elapsed (0 = fresh, 1 = about to reset)."""
        if self.time_remaining_seconds is None or self.window_total_seconds is None:
            return None
        if self.window_total_seconds <= 0:
            return None
        elapsed = self.window_total_seconds - self.time_remaining_seconds
        return max(0.0, min(1.0, elapsed / self.window_total_seconds))


@dataclass
class _ClaudeSessionAggregate:
    sessions: int = 0
    messages: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    oldest_in_window: datetime | None = None
    timestamps: list[datetime] = field(default_factory=list)
    source: str = "session-meta"


def _read_claude_sessions(
    window_days: int = 7, cutoff: datetime | None = None
) -> _ClaudeSessionAggregate:
    """Aggregate session-meta files newer than ``cutoff`` (or last ``window_days``)."""
    agg = _ClaudeSessionAggregate()
    meta_dir = Path.home() / ".claude" / "usage-data" / "session-meta"
    if not meta_dir.is_dir():
        return agg

    if cutoff is None:
        cutoff = datetime.now(UTC) - timedelta(days=window_days)

    for path in meta_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text())
            start = data.get("start_time", "")
            if not start:
                continue
            ts = datetime.fromisoformat(start.replace("Z", "+00:00"))
            if ts < cutoff:
                continue
            agg.sessions += 1
            agg.messages += data.get("user_message_count", 0) + data.get(
                "assistant_message_count", 0
            )
            agg.tool_calls += sum(data.get("tool_counts", {}).values())
            agg.input_tokens += data.get("input_tokens", 0) or 0
            agg.output_tokens += data.get("output_tokens", 0) or 0
            agg.timestamps.append(ts)
            if agg.oldest_in_window is None or ts < agg.oldest_in_window:
                agg.oldest_in_window = ts
        except (json.JSONDecodeError, OSError, ValueError, KeyError):
            continue

    return agg


def _read_claude_history(
    window_days: int = 7, cutoff: datetime | None = None
) -> _ClaudeSessionAggregate:
    """Parse ``~/.claude/history.jsonl`` for recent message activity.

    Claude Code writes one line per user/assistant turn to history.jsonl with
    an epoch-ms timestamp and ``sessionId``. It's the freshest local source
    when session-meta is stale and stats-cache hasn't been recomputed (the
    stats-cache job only runs on session exit). We count lines as "messages"
    and unique ``sessionId`` values as "sessions".
    """
    agg = _ClaudeSessionAggregate(source="history")
    hist_path = Path.home() / ".claude" / "history.jsonl"
    if not hist_path.is_file():
        return agg

    if cutoff is None:
        cutoff = datetime.now(UTC) - timedelta(days=window_days)
    cutoff_ms = int(cutoff.timestamp() * 1000)
    session_ids: set[str] = set()
    oldest_ms: int | None = None
    count = 0
    try:
        with hist_path.open(encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                if not raw.strip():
                    continue
                try:
                    entry = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                ts = entry.get("timestamp")
                if not isinstance(ts, int) or ts < cutoff_ms:
                    continue
                count += 1
                sid = entry.get("sessionId")
                if isinstance(sid, str):
                    session_ids.add(sid)
                if oldest_ms is None or ts < oldest_ms:
                    oldest_ms = ts
    except OSError:
        return agg

    agg.messages = count
    agg.sessions = len(session_ids)
    if oldest_ms is not None:
        agg.oldest_in_window = datetime.fromtimestamp(oldest_ms / 1000, tz=UTC)
    return agg


def _read_claude_stats_cache(window_days: int = 7) -> _ClaudeSessionAggregate:
    """Fallback: aggregate ~/.claude/stats-cache.json dailyActivity for the window."""
    agg = _ClaudeSessionAggregate(source="stats-cache")
    cache_path = Path.home() / ".claude" / "stats-cache.json"
    if not cache_path.is_file():
        return agg

    try:
        data = json.loads(cache_path.read_text())
    except (json.JSONDecodeError, OSError):
        return agg

    today = datetime.now(UTC).date()
    cutoff = today - timedelta(days=window_days)
    oldest_day: date | None = None

    for entry in data.get("dailyActivity", []):
        try:
            day = date.fromisoformat(entry.get("date", ""))
        except (ValueError, TypeError):
            continue
        if day < cutoff or day > today:
            continue
        agg.sessions += int(entry.get("sessionCount", 0) or 0)
        agg.messages += int(entry.get("messageCount", 0) or 0)
        agg.tool_calls += int(entry.get("toolCallCount", 0) or 0)
        if oldest_day is None or day < oldest_day:
            oldest_day = day

    if oldest_day is not None:
        agg.oldest_in_window = datetime.combine(oldest_day, datetime.min.time(), tzinfo=UTC)
    return agg


def _read_claude_subscription() -> dict[str, str | None]:
    """Read Claude subscription type and tier from the SSO credentials file."""
    cred_path = Path.home() / ".claude" / ".credentials.json"
    try:
        data = json.loads(cred_path.read_text())
        oauth = data.get("claudeAiOauth", {})
        return {
            "subscription": oauth.get("subscriptionType"),
            "tier": oauth.get("rateLimitTier"),
        }
    except (FileNotFoundError, json.JSONDecodeError, OSError, KeyError):
        return {"subscription": None, "tier": None}


def _read_claude_window(
    cutoff: datetime, *, allow_stats_cache: bool = True
) -> _ClaudeSessionAggregate:
    """Aggregate Claude activity newer than ``cutoff`` from the freshest source.

    Tries session-meta first, then history.jsonl, and finally stats-cache
    (day-granularity, so only useful for multi-day windows -- callers with a
    sub-day window should pass ``allow_stats_cache=False``).
    """
    agg = _read_claude_sessions(cutoff=cutoff)
    if agg.messages == 0 and agg.sessions == 0:
        agg = _read_claude_history(cutoff=cutoff)
    if allow_stats_cache and agg.messages == 0 and agg.sessions == 0:
        # stats-cache is day-granular; the window_days arg is what it expects.
        # The caller only enables this for windows that are >= 1 day.
        days = max(1, int((datetime.now(UTC) - cutoff).total_seconds() // 86400))
        agg = _read_claude_stats_cache(window_days=days)
    return agg


def _derive_status(messages: int, sessions: int, limit: int | None) -> str:
    """Map raw counts + limit to a status bucket used by the renderer."""
    if messages == 0 and sessions == 0:
        return "empty"
    if limit is not None and limit > 0:
        fraction = messages / limit
        if fraction >= 0.9:
            return "critical"
        if fraction >= 0.7:
            return "warning"
    return "ok"


def fetch_claude_code_usage(
    window_days: int = _DEFAULT_WINDOW_DAYS,
    limit: int | None = None,
) -> UsageStats:
    """Claude Code activity with both 5-hour and 7-day rolling windows.

    Claude Max caps apply over two rolling windows: a 5-hour active-session
    window and a 7-day weekly window. This reads the local session-meta and
    history.jsonl (stats-cache as a day-granular fallback for the weekly
    window only) and returns both usages side by side.

    ``window_days`` controls the longer (weekly) window; it stays parametric
    so test fixtures can shrink it. The 5-hour window is fixed at 5 hours.
    """
    now = datetime.now(UTC)
    week_cutoff = now - timedelta(days=window_days)
    hour5_cutoff = now - timedelta(seconds=_FIVE_HOUR_SECONDS)

    try:
        week_agg = _read_claude_window(week_cutoff)
        hour5_agg = _read_claude_window(hour5_cutoff, allow_stats_cache=False)
        sub = _read_claude_subscription()
    except Exception:
        return UsageStats(
            provider="claude_code",
            display_name="Claude Code",
            window_days=window_days,
            status="unknown",
            error="failed to read stats",
        )

    tier = sub.get("tier")
    week_limit = limit
    if week_limit is None and tier:
        week_limit = _CLAUDE_TIER_WEEKLY_LIMITS.get(tier)
    hour5_limit: int | None = None
    if tier:
        hour5_limit = _CLAUDE_TIER_5H_LIMITS.get(tier)

    window_total_seconds = window_days * 86400
    time_remaining_seconds: int | None = None
    if week_agg.oldest_in_window is not None:
        age = (now - week_agg.oldest_in_window).total_seconds()
        time_remaining_seconds = max(0, int(window_total_seconds - age))
    else:
        time_remaining_seconds = window_total_seconds

    # The overall status is driven by the tighter of the two windows so the
    # widget's severity colour matches the window the user is actually about
    # to hit first.
    week_status = _derive_status(week_agg.messages, week_agg.sessions, week_limit)
    hour5_status = _derive_status(hour5_agg.messages, hour5_agg.sessions, hour5_limit)
    severity_order = {"empty": 0, "ok": 1, "warning": 2, "critical": 3}
    status = (
        hour5_status if severity_order[hour5_status] >= severity_order[week_status] else week_status
    )
    # If both windows are empty, keep the "empty" label.
    if week_agg.messages == 0 and week_agg.sessions == 0 and hour5_agg.messages == 0:
        status = "empty"

    tier_label = None
    if sub.get("subscription"):
        tier_label = str(sub["subscription"]).title()
        if tier and "20x" in tier:
            tier_label += " 20x"
        elif tier and "5x" in tier:
            tier_label += " 5x"

    note: str | None = None
    if status == "empty":
        note = "no local activity tracked (account usage is not exposed via SSO)"
    elif week_agg.source == "stats-cache":
        note = "from stats-cache (session-meta empty)"

    return UsageStats(
        provider="claude_code",
        display_name="Claude Code",
        window_days=window_days,
        messages=week_agg.messages,
        sessions=week_agg.sessions,
        tool_calls=week_agg.tool_calls,
        input_tokens=week_agg.input_tokens,
        output_tokens=week_agg.output_tokens,
        limit=week_limit,
        time_remaining_seconds=time_remaining_seconds,
        window_total_seconds=window_total_seconds,
        tier=tier_label,
        status=status,
        note=note,
        hour5_used=hour5_agg.messages,
        hour5_limit=hour5_limit,
        week7_used=week_agg.messages,
        week7_limit=week_limit,
    )


def _gh_has_copilot() -> bool:
    """Detect Copilot availability for the authenticated gh user."""
    if shutil.which("gh") is None:
        return False
    try:
        ext = subprocess.run(
            ["gh", "extension", "list"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if ext.returncode == 0 and "copilot" in ext.stdout.lower():
            return True
    except (subprocess.TimeoutExpired, OSError):
        pass
    try:
        probe = subprocess.run(
            ["gh", "copilot", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if probe.returncode == 0 and "copilot" in probe.stdout.lower():
            return True
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        pass
    try:
        seats = subprocess.run(
            ["gh", "api", "/user/copilot_billing"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if seats.returncode == 0 and seats.stdout.strip().startswith("{"):
            return True
    except (subprocess.TimeoutExpired, OSError):
        pass
    return False


def _gh_copilot_billing() -> dict | None:
    """Query ``gh api /user/copilot/billing`` for plan info. None on failure."""
    if shutil.which("gh") is None:
        return None
    try:
        result = subprocess.run(
            ["gh", "api", "/user/copilot/billing"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def fetch_copilot_usage(window_days: int = 7, limit: int | None = None) -> UsageStats:
    """GitHub Copilot presence via gh CLI -- uses the existing ``gh auth`` token (SSO)."""
    if shutil.which("gh") is None:
        return UsageStats(
            provider="copilot",
            display_name="Copilot",
            window_days=window_days,
            status="unconfigured",
            note="gh CLI not installed",
        )
    billing = _gh_copilot_billing()
    if billing:
        # User has a Copilot subscription reachable via gh auth.
        plan = str(billing.get("copilot_plan") or billing.get("plan") or "subscribed")
        last_activity = billing.get("last_activity_at") or billing.get("updated_at")
        note: str | None = None
        if last_activity:
            note = f"last activity {str(last_activity)[:10]}"
        return UsageStats(
            provider="copilot",
            display_name="Copilot",
            window_days=window_days,
            status="ok",
            tier=plan,
            note=note or "per-seat message usage not exposed by the GH API",
        )
    if _gh_has_copilot():
        return UsageStats(
            provider="copilot",
            display_name="Copilot",
            window_days=window_days,
            status="unknown",
            tier="subscribed",
            note="per-seat usage not in public GH API",
        )
    return UsageStats(
        provider="copilot",
        display_name="Copilot",
        window_days=window_days,
        status="unconfigured",
        note="no Copilot subscription detected",
    )


def _openai_usage_request(
    api_key: str,
    org_id: str | None,
    start_time: int,
) -> dict:
    """Call the OpenAI organization usage endpoint. Requires an admin key."""
    params = urllib.parse.urlencode(
        {
            "start_time": start_time,
            "bucket_width": "1d",
            "limit": 32,
        }
    )
    url = f"https://api.openai.com/v1/organization/usage/completions?{params}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {api_key}")
    if org_id:
        req.add_header("OpenAI-Organization", org_id)
    with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310  # nosec B310  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
        payload: dict = json.loads(resp.read().decode("utf-8"))
    return payload


def _resolve_openai_key() -> str | None:
    """Resolve an OpenAI admin key from env, .env, keyring, or common config paths.

    OpenAI does not offer an SSO-based token flow for API clients, so the
    best we can do is look in the places a user with proper secret hygiene
    would store the key: environment variables, the project .env file, the
    OS keyring (Windows Credential Manager / macOS Keychain / libsecret),
    and common CLI config files.
    """
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        return key
    # Check .env file (same file the dashboard reads GH_TOKEN from).
    from dotenv import dotenv_values

    env_values = dotenv_values(".env")
    dotenv_key = env_values.get("OPENAI_API_KEY")
    if dotenv_key:
        return dotenv_key
    # Try OS keyring if the optional dependency is installed.
    try:
        import keyring  # type: ignore[import-not-found]

        for service, user in (
            ("openai", "api_key"),
            ("openai", "OPENAI_API_KEY"),
            ("OpenAI", "api_key"),
        ):
            try:
                value = keyring.get_password(service, user)
            except Exception:
                value = None
            if isinstance(value, str) and value:
                return value
    except ImportError:
        pass
    # Common CLI config locations.
    for candidate in (
        Path.home() / ".openai" / "api_key",
        Path.home() / ".config" / "openai" / "api_key",
    ):
        try:
            if candidate.is_file():
                value = candidate.read_text(encoding="utf-8").strip()
                if value:
                    return value
        except OSError:
            continue
    return None


def _resolve_openai_keys() -> list[tuple[str, str, str | None]]:
    """Discover all OpenAI API keys from env and .env.

    Returns a list of (label, api_key, org_id) tuples.  Supports:
      - OPENAI_API_KEY_<LABEL> entries (one per account, label from suffix)
      - Legacy single OPENAI_API_KEY (labelled "OpenAI")

    Keys from .env are merged with os.environ; explicit env vars win on
    conflict.  The suffix after ``OPENAI_API_KEY_`` becomes the display
    label (title-cased).
    """
    from dotenv import dotenv_values

    env_values = dotenv_values(".env")

    # Merge: explicit env wins over .env for the same var name.
    merged: dict[str, str] = {k: v for k, v in env_values.items() if v}
    merged.update({k: v for k, v in os.environ.items() if v})

    # Collect OPENAI_API_KEY_<SUFFIX> entries.
    prefix = "OPENAI_API_KEY_"
    accounts: list[tuple[str, str, str | None]] = []
    seen_keys: set[str] = set()
    for var, value in sorted(merged.items()):
        if var.startswith(prefix) and len(var) > len(prefix):
            suffix = var[len(prefix) :]
            label = suffix.replace("_", " ").title()
            org_var = f"OPENAI_ORG_ID_{suffix}"
            org_id = merged.get(org_var)
            if value not in seen_keys:
                accounts.append((label, value, org_id))
                seen_keys.add(value)

    if accounts:
        return accounts

    # Fallback: single legacy OPENAI_API_KEY.
    single = _resolve_openai_key()
    if single:
        org_id = merged.get("OPENAI_ORG_ID") or merged.get("OPENAI_ORGANIZATION")
        return [("OpenAI", single, org_id)]

    return []


def fetch_openai_usage(
    window_days: int = 7,
    limit: int | None = None,
    *,
    api_key: str | None = None,
    org_id: str | None = None,
    label: str = "OpenAI",
) -> UsageStats:
    """OpenAI usage for a single account.

    When called without explicit ``api_key``, falls back to the legacy
    single-key resolution for backward compatibility.
    """
    if api_key is None:
        api_key = _resolve_openai_key()
    if org_id is None and api_key is not None:
        org_id = os.environ.get("OPENAI_ORG_ID") or os.environ.get("OPENAI_ORGANIZATION")

    if not api_key:
        return UsageStats(
            provider="openai",
            display_name=label,
            window_days=window_days,
            status="unconfigured",
            error="no key in env/keyring/~/.openai -- OpenAI has no SSO",
        )

    start_time = int((datetime.now(UTC) - timedelta(days=window_days)).timestamp())
    try:
        payload = _openai_usage_request(api_key, org_id, start_time)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return UsageStats(
                provider="openai",
                display_name=label,
                window_days=window_days,
                status="unavailable",
                note="requires admin API key",
            )
        body_tail = ""
        try:
            body_tail = exc.read().decode("utf-8", errors="replace")[:120]
        except Exception:
            pass
        detail = f"HTTP {exc.code}"
        if body_tail:
            detail += f": {body_tail.strip()}"
        return UsageStats(
            provider="openai",
            display_name=label,
            window_days=window_days,
            status="unavailable",
            note=detail,
        )
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        return UsageStats(
            provider="openai",
            display_name=label,
            window_days=window_days,
            status="unavailable",
            note=f"{exc.__class__.__name__}",
        )

    messages = 0
    input_tokens = 0
    output_tokens = 0
    for bucket in payload.get("data", []):
        for result in bucket.get("results", []):
            messages += int(result.get("num_model_requests", 0) or 0)
            input_tokens += int(result.get("input_tokens", 0) or 0)
            output_tokens += int(result.get("output_tokens", 0) or 0)

    window_total_seconds = window_days * 86400
    return UsageStats(
        provider="openai",
        display_name=label,
        window_days=window_days,
        messages=messages,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        limit=limit,
        window_total_seconds=window_total_seconds,
        time_remaining_seconds=window_total_seconds,
        tier=(org_id or "personal") if messages else (org_id or "personal"),
        status="ok" if messages or input_tokens or output_tokens else "empty",
        note=None if messages else "no usage in window",
    )


def fetch_all_usage(
    claude_limit: int | None = None,
    openai_limit: int | None = None,
    copilot_limit: int | None = None,
) -> list[UsageStats]:
    """Fetch usage from all providers.

    OpenAI accounts are discovered dynamically from OPENAI_API_KEY_<LABEL>
    entries in the environment or .env file. Each gets its own usage row.

    Individual provider failures are caught per-provider so one failure does
    not affect others. Failed providers are included with status="unavailable".
    """
    results: list[UsageStats] = []

    # Claude
    try:
        results.append(fetch_claude_code_usage(limit=claude_limit))
    except Exception:
        results.append(
            UsageStats(
                provider="claude_code",
                display_name="Claude Code",
                status="unavailable",
                note="failed to fetch usage",
            )
        )

    # OpenAI (one or more accounts)
    try:
        openai_accounts = _resolve_openai_keys()
        if openai_accounts:
            for label, key, org_id in openai_accounts:
                try:
                    results.append(
                        fetch_openai_usage(
                            limit=openai_limit, api_key=key, org_id=org_id, label=label
                        )
                    )
                except Exception:
                    results.append(
                        UsageStats(
                            provider="openai",
                            display_name=label,
                            status="unavailable",
                            note="failed to fetch usage",
                        )
                    )
        else:
            results.append(fetch_openai_usage(limit=openai_limit))
    except Exception:
        results.append(
            UsageStats(
                provider="openai",
                display_name="OpenAI",
                status="unavailable",
                note="failed to fetch usage",
            )
        )

    # Copilot
    try:
        results.append(fetch_copilot_usage(limit=copilot_limit))
    except Exception:
        results.append(
            UsageStats(
                provider="copilot",
                display_name="Copilot",
                status="unavailable",
                note="failed to fetch usage",
            )
        )

    return results


def claude_daily_message_buckets(window_days: int = 7) -> list[int]:
    """Return per-day Claude message counts for the trailing ``window_days``.

    Oldest day first; length == ``window_days``. Reads ``history.jsonl`` (the
    freshest source) with a fallback to ``stats-cache.json``. Used by the
    dashboard's activity sparkline. Returns all-zeros when no data.
    """
    buckets = [0] * window_days
    today = datetime.now(UTC).date()
    hist_path = Path.home() / ".claude" / "history.jsonl"
    if hist_path.is_file():
        try:
            with hist_path.open(encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    if not raw.strip():
                        continue
                    try:
                        entry = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    ts = entry.get("timestamp")
                    if not isinstance(ts, int):
                        continue
                    day = datetime.fromtimestamp(ts / 1000, tz=UTC).date()
                    days_ago = (today - day).days
                    if 0 <= days_ago < window_days:
                        buckets[window_days - 1 - days_ago] += 1
            if any(buckets):
                return buckets
        except OSError:
            pass

    # Fallback: stats-cache
    cache_path = Path.home() / ".claude" / "stats-cache.json"
    if cache_path.is_file():
        try:
            data = json.loads(cache_path.read_text())
        except (json.JSONDecodeError, OSError):
            return buckets
        for entry in data.get("dailyActivity", []):
            try:
                day = date.fromisoformat(entry.get("date", ""))
            except (ValueError, TypeError):
                continue
            days_ago = (today - day).days
            if 0 <= days_ago < window_days:
                buckets[window_days - 1 - days_ago] = int(entry.get("messageCount", 0) or 0)
    return buckets
