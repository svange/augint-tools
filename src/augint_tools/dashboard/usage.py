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

# Rough per-tier weekly message estimates for Claude subscription tiers.
# These are conservative/approximate; users can still see raw counts when unknown.
_CLAUDE_TIER_WEEKLY_LIMITS: dict[str, int] = {
    "default_claude_pro": 1500,
    "default_claude_max_5x": 7500,
    "default_claude_max_20x": 30000,
    "default_claude_team": 10000,
    "default_claude_enterprise": 50000,
}


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
    status: str = "ok"  # ok, warning, critical, unknown, unconfigured, empty
    error: str | None = None
    # Free-form note shown under the progress bar (e.g. data source).
    note: str | None = None

    @property
    def usage_fraction(self) -> float | None:
        """Return usage as a fraction of the limit, or None if no limit set."""
        if self.limit is None or self.limit <= 0:
            return None
        return min(1.0, self.messages / self.limit)

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


def _read_claude_sessions(window_days: int = 7) -> _ClaudeSessionAggregate:
    """Aggregate session-meta files from the last N days, tracking oldest message."""
    agg = _ClaudeSessionAggregate()
    meta_dir = Path.home() / ".claude" / "usage-data" / "session-meta"
    if not meta_dir.is_dir():
        return agg

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


def _read_claude_history(window_days: int = 7) -> _ClaudeSessionAggregate:
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

    cutoff_ms = int((datetime.now(UTC) - timedelta(days=window_days)).timestamp() * 1000)
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


def fetch_claude_code_usage(
    window_days: int = 7,
    limit: int | None = None,
) -> UsageStats:
    """Claude Code activity from local session-meta (or stats-cache fallback)."""
    try:
        agg = _read_claude_sessions(window_days)
        if agg.messages == 0 and agg.sessions == 0:
            agg = _read_claude_history(window_days)
        if agg.messages == 0 and agg.sessions == 0:
            agg = _read_claude_stats_cache(window_days)
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
    if limit is None and tier:
        limit = _CLAUDE_TIER_WEEKLY_LIMITS.get(tier)

    window_total_seconds = window_days * 86400
    time_remaining_seconds: int | None = None
    if agg.oldest_in_window is not None:
        age = (datetime.now(UTC) - agg.oldest_in_window).total_seconds()
        time_remaining_seconds = max(0, int(window_total_seconds - age))
    else:
        time_remaining_seconds = window_total_seconds

    status = "ok"
    if agg.messages == 0 and agg.sessions == 0:
        status = "empty"
    elif limit is not None and limit > 0:
        fraction = agg.messages / limit
        if fraction >= 0.9:
            status = "critical"
        elif fraction >= 0.7:
            status = "warning"

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
    elif agg.source == "stats-cache":
        note = "from stats-cache (session-meta empty)"

    return UsageStats(
        provider="claude_code",
        display_name="Claude Code",
        window_days=window_days,
        messages=agg.messages,
        sessions=agg.sessions,
        tool_calls=agg.tool_calls,
        input_tokens=agg.input_tokens,
        output_tokens=agg.output_tokens,
        limit=limit,
        time_remaining_seconds=time_remaining_seconds,
        window_total_seconds=window_total_seconds,
        tier=tier_label,
        status=status,
        note=note,
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
            error="gh CLI not installed -- copilot auth rides on gh",
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
        error="no Copilot subscription on gh-authed account",
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
    """Resolve an OpenAI admin key from env, keyring, or common config paths.

    OpenAI does not offer an SSO-based token flow for API clients, so the
    best we can do is look in the places a user with proper secret hygiene
    would store the key: the OS keyring (Windows Credential Manager / macOS
    Keychain / libsecret) and common CLI config files.
    """
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        return key
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


def fetch_openai_usage(window_days: int = 7, limit: int | None = None) -> UsageStats:
    """OpenAI usage via admin key (env, keyring, or config file)."""
    api_key = _resolve_openai_key()
    org_id = os.environ.get("OPENAI_ORG_ID") or os.environ.get("OPENAI_ORGANIZATION")

    if not api_key:
        return UsageStats(
            provider="openai",
            display_name="OpenAI",
            window_days=window_days,
            status="unconfigured",
            error="no key in env/keyring/~/.openai -- OpenAI has no SSO",
        )

    start_time = int((datetime.now(UTC) - timedelta(days=window_days)).timestamp())
    try:
        payload = _openai_usage_request(api_key, org_id, start_time)
    except urllib.error.HTTPError as exc:
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
            display_name="OpenAI",
            window_days=window_days,
            status="unknown",
            error=detail,
        )
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        return UsageStats(
            provider="openai",
            display_name="OpenAI",
            window_days=window_days,
            status="unknown",
            error=f"{exc.__class__.__name__}",
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
        display_name="OpenAI",
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
    """Fetch usage from all providers."""
    return [
        fetch_claude_code_usage(limit=claude_limit),
        fetch_openai_usage(limit=openai_limit),
        fetch_copilot_usage(limit=copilot_limit),
    ]


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
