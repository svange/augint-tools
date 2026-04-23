"""YAML-driven compliance check engine.

The engine reads a ``standards.yaml`` document authored alongside the standards
themselves (``augmenting-integrations/ai-cc-tools/plugins/ai-standardize/standards.yaml``)
and evaluates each declared check against the pre-fetched ``FetchContext``. Each
check yields one ``HealthCheckResult`` so the TUI can render every standard as
its own finding on a repo card.

The YAML is fetched from GitHub's contents API using the same PyGithub requester
that serves the dashboard's GraphQL queries. Results are cached in-process for
``_CACHE_TTL_SECONDS`` so every refresh cycle pays at most one network hit for
the standards document.

Four built-in check types cover the bulk of declarative standards, plus a
``handler`` escape hatch that dispatches to registered Python functions for
anything that requires AWS, HTTP probes, or other external data. Handlers live
in :mod:`augint_tools.dashboard.health._handlers`.
"""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import yaml
from loguru import logger

from ._models import HealthCheckResult, Severity

if TYPE_CHECKING:
    from github import Github

    from . import FetchContext

# Default location of the canonical standards document. Always-latest-from-main
# per the trust-boundary decision: the tool is internal-only, the user controls
# what lands on main.
DEFAULT_STANDARDS_URL = (
    "https://api.github.com/repos/augmenting-integrations/ai-cc-tools/"
    "contents/plugins/ai-standardize/standards.yaml?ref=main"
)

# Cache TTL for the standards document. Short enough that edits surface on the
# next scheduled refresh; long enough that rapid refreshes don't re-fetch.
_CACHE_TTL_SECONDS = 3600


# ---------------------------------------------------------------------------
# Severity parsing
# ---------------------------------------------------------------------------


_SEVERITY_BY_NAME: dict[str, Severity] = {
    "CRITICAL": Severity.CRITICAL,
    "HIGH": Severity.HIGH,
    "MEDIUM": Severity.MEDIUM,
    "LOW": Severity.LOW,
    "OK": Severity.OK,
}


def _parse_severity(value: Any) -> Severity:
    if isinstance(value, str) and value.upper() in _SEVERITY_BY_NAME:
        return _SEVERITY_BY_NAME[value.upper()]
    # Any unknown value defaults to MEDIUM so an authoring mistake is visible
    # but not catastrophic.
    return Severity.MEDIUM


# ---------------------------------------------------------------------------
# Standards document loader
# ---------------------------------------------------------------------------


@dataclass
class _CacheEntry:
    fetched_at: float
    document: dict


_cache: dict[str, _CacheEntry] = {}


def _fetch_standards_yaml(gh: Github, url: str) -> dict:
    """Fetch and decode the standards YAML via the GitHub contents API.

    Response shape: ``{"content": "<base64>", "encoding": "base64", ...}``.
    Uses PyGithub's internal requester so the same auth token / session as the
    dashboard's GraphQL queries applies.
    """
    # ``requestJsonAndCheck`` handles auth, retries, and rate-limit headers.
    requester = gh.requester
    path = url.replace("https://api.github.com", "")
    _headers, data = requester.requestJsonAndCheck("GET", path)
    content = data.get("content") or ""
    encoding = data.get("encoding") or "base64"
    if encoding != "base64":
        raise ValueError(f"unsupported standards.yaml encoding: {encoding}")
    decoded = base64.b64decode(content).decode("utf-8")
    doc = yaml.safe_load(decoded)
    if not isinstance(doc, dict):
        raise ValueError("standards.yaml must be a mapping at the root")
    return doc


def load_standards(
    gh: Github | None,
    url: str | None = None,
    *,
    force_refresh: bool = False,
) -> dict | None:
    """Return the parsed standards document, cached in-process.

    Returns ``None`` when ``gh`` is not available (tests / offline mode); the
    engine then emits a single informational result and skips evaluation.
    """
    if gh is None:
        return None
    target = url or DEFAULT_STANDARDS_URL
    now = time.time()
    cached = _cache.get(target)
    if not force_refresh and cached is not None and (now - cached.fetched_at) < _CACHE_TTL_SECONDS:
        return cached.document
    try:
        doc = _fetch_standards_yaml(gh, target)
    except Exception as exc:
        logger.warning(
            "compliance engine: failed to fetch {} ({}: {}); using cache if any",
            target,
            exc.__class__.__name__,
            exc,
        )
        return cached.document if cached else None
    _cache[target] = _CacheEntry(fetched_at=now, document=doc)
    return doc


def clear_cache() -> None:
    """Drop the in-process standards cache. Intended for tests."""
    _cache.clear()


# ---------------------------------------------------------------------------
# Template substitution
# ---------------------------------------------------------------------------


def _format_template(value: Any, vars: dict[str, str]) -> Any:
    """Recursively substitute ``{var}`` placeholders in strings.

    Missing keys are left as-is so typos surface in the TUI rather than
    crashing the refresh loop.
    """
    if isinstance(value, str):
        try:
            return value.format(**vars)
        except (KeyError, IndexError):
            return value
    if isinstance(value, dict):
        return {k: _format_template(v, vars) for k, v in value.items()}
    if isinstance(value, list):
        return [_format_template(v, vars) for v in value]
    return value


# ---------------------------------------------------------------------------
# Built-in check types
# ---------------------------------------------------------------------------


def _check_file_exists(
    context: FetchContext,
    params: dict,
) -> tuple[bool, str]:
    """Pass when the named pre-fetched file is present."""
    file_key = params.get("file")
    text = _resolve_file_text(context, file_key)
    if text is None:
        return False, f"{file_key} not present in repo"
    return True, f"{file_key} present"


def _check_file_absent(
    context: FetchContext,
    params: dict,
) -> tuple[bool, str]:
    """Pass when the named pre-fetched file is NOT present."""
    file_key = params.get("file")
    text = _resolve_file_text(context, file_key)
    if text is None:
        return True, f"{file_key} absent"
    return False, f"{file_key} present (should be absent)"


def _check_file_content_matches(
    context: FetchContext,
    params: dict,
) -> tuple[bool, str]:
    """Pass when the named file's content matches regex / value assertions.

    Supported params:
      file: "pyproject.toml" | "pipeline.yaml" | "package.json" | "precommit"
      pattern: regex (Python syntax)
      capture: optional int (group to extract; default 1)
      assert:
        type: "present" | "absent" | "min_value" | "max_value" | "equals"
        value: numeric or string when applicable
    """
    file_key = params.get("file")
    pattern = params.get("pattern")
    text = _resolve_file_text(context, file_key)
    if text is None:
        return False, f"{file_key} not present"
    if not isinstance(pattern, str):
        return False, "check misconfigured: pattern required"
    match = re.search(pattern, text, re.MULTILINE)
    assertion = params.get("assert") or {}
    atype = assertion.get("type", "present")
    if atype == "present":
        return (True, "pattern present") if match else (False, "pattern absent")
    if atype == "absent":
        return (False, "pattern present (should be absent)") if match else (True, "pattern absent")
    if not match:
        return False, "pattern not found"
    group = int(assertion.get("group", params.get("capture", 1)))
    try:
        captured = match.group(group)
    except IndexError:
        return False, "capture group missing"
    if atype == "equals":
        expected = assertion.get("value")
        return (
            (True, "value equals")
            if str(captured) == str(expected)
            else (
                False,
                f"value={captured} expected={expected}",
            )
        )
    try:
        num = int(captured)
    except (TypeError, ValueError):
        return False, f"captured value not numeric: {captured}"
    target_raw = assertion.get("value")
    try:
        target = int(target_raw) if target_raw is not None else None
    except (TypeError, ValueError):
        return False, f"check misconfigured: non-numeric target {target_raw}"
    if target is None:
        return False, "check misconfigured: missing target value"
    if atype == "min_value":
        return (
            (True, f"value {num} >= {target}")
            if num >= target
            else (
                False,
                f"value {num} < {target}",
            )
        )
    if atype == "max_value":
        return (
            (True, f"value {num} <= {target}")
            if num <= target
            else (
                False,
                f"value {num} > {target}",
            )
        )
    return False, f"unknown assert type: {atype}"


def _check_workflow_job_has_step(
    context: FetchContext,
    params: dict,
) -> tuple[bool, str]:
    """Pass when a named job in pipeline.yaml contains a matching step.

    Params:
      job: job name (e.g. "security")
      step_matches:
        run_contains_any: [str, ...]  # substring-OR against step.run
        uses_contains_any: [str, ...] # substring-OR against step.uses
    """
    raw = context.pipeline_text
    if raw is None:
        return False, "pipeline.yaml not present"
    try:
        wf = yaml.safe_load(raw)
    except yaml.YAMLError:
        return False, "pipeline.yaml parse error"
    if not isinstance(wf, dict):
        return False, "pipeline.yaml not a mapping"
    jobs = wf.get("jobs")
    if not isinstance(jobs, dict):
        return False, "pipeline.yaml has no jobs"
    job_name = params.get("job")
    job = jobs.get(job_name)
    if not isinstance(job, dict):
        return False, f"job {job_name!r} not found"
    steps = job.get("steps") or []
    matches = params.get("step_matches") or {}
    run_any = matches.get("run_contains_any") or []
    uses_any = matches.get("uses_contains_any") or []
    for step in steps:
        if not isinstance(step, dict):
            continue
        run = step.get("run") or ""
        uses = step.get("uses") or ""
        if isinstance(run, str) and any(s in run for s in run_any):
            return True, f"job {job_name} contains matching run step"
        if isinstance(uses, str) and any(s in uses for s in uses_any):
            return True, f"job {job_name} contains matching uses step"
    return False, f"job {job_name} missing required step"


# Shell suffixes that swallow non-zero exit codes. ``|| true``, ``|| :``,
# ``|| echo ...``. Mirrors the pattern used by the legacy coverage check.
_CHEAT_SUFFIX_RE = re.compile(r"\|\|\s*(true|:|echo\b)")
_SET_PLUS_E_RE = re.compile(r"(?m)^\s*set\s+(?:\+e\b|\+o\s+(?:pipefail|errexit)\b)")


def _check_workflow_all_jobs_scan(
    context: FetchContext,
    params: dict,
) -> tuple[bool, str]:
    """Scan every job/step in pipeline.yaml for cheat patterns.

    Params:
      reject_patterns: optional list of regexes applied to step.run
      reject_continue_on_error: bool (default True)
      reject_shell_suppress: bool (default True)  # the ``|| true`` family
      reject_set_plus_e: bool (default True)
    """
    raw = context.pipeline_text
    if raw is None:
        return True, "pipeline.yaml not present"
    try:
        wf = yaml.safe_load(raw)
    except yaml.YAMLError:
        return False, "pipeline.yaml parse error"
    jobs = (wf or {}).get("jobs") if isinstance(wf, dict) else None
    if not isinstance(jobs, dict):
        return True, "pipeline.yaml has no jobs"
    custom_patterns: list[re.Pattern[str]] = [
        re.compile(p) for p in (params.get("reject_patterns") or []) if isinstance(p, str)
    ]
    block_coe = params.get("reject_continue_on_error", True)
    block_suppress = params.get("reject_shell_suppress", True)
    block_set_plus_e = params.get("reject_set_plus_e", True)
    offenders: list[str] = []
    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        if block_coe and bool(job.get("continue-on-error")):
            offenders.append(f"{job_name}: continue-on-error")
        for step in job.get("steps") or []:
            if not isinstance(step, dict):
                continue
            if block_coe and bool(step.get("continue-on-error")):
                offenders.append(f"{job_name}: step continue-on-error")
            run = step.get("run") or ""
            if isinstance(run, str):
                if block_suppress and _CHEAT_SUFFIX_RE.search(run):
                    offenders.append(f"{job_name}: '|| true' family")
                if block_set_plus_e and _SET_PLUS_E_RE.search(run):
                    offenders.append(f"{job_name}: set +e")
                for pat in custom_patterns:
                    if pat.search(run):
                        offenders.append(f"{job_name}: matched {pat.pattern}")
    if offenders:
        return False, "; ".join(offenders[:3]) + (
            f" (+{len(offenders) - 3} more)" if len(offenders) > 3 else ""
        )
    return True, "no cheat patterns detected"


def _check_ruleset_has_required_checks(
    context: FetchContext,
    params: dict,
) -> tuple[bool, str]:
    """Verify a ruleset includes the expected required_status_checks contexts.

    Params:
      ruleset_name: optional; defaults to first ruleset whose target matches ``target``
      target: "branch" | "tag" | None (None matches any)
      expected_contexts: list of status check context names
    """
    rulesets = context.rulesets or []
    if not rulesets:
        return False, "no rulesets configured"
    target = params.get("target")
    desired_name = params.get("ruleset_name")
    expected: list[str] = list(params.get("expected_contexts") or [])
    if not expected:
        return False, "check misconfigured: expected_contexts empty"
    # Pick the ruleset to inspect.
    ruleset = None
    for rs in rulesets:
        if desired_name and rs.get("name") != desired_name:
            continue
        if target and rs.get("target") != target.upper():
            continue
        ruleset = rs
        break
    if ruleset is None:
        return False, "no matching ruleset found"
    contexts: set[str] = set()
    for rule in (ruleset.get("rules") or {}).get("nodes") or []:
        if not isinstance(rule, dict):
            continue
        if rule.get("type") != "REQUIRED_STATUS_CHECKS":
            continue
        params_blob = rule.get("parameters")
        if isinstance(params_blob, str):
            try:
                params_blob = json.loads(params_blob)
            except json.JSONDecodeError:
                params_blob = {}
        if not isinstance(params_blob, dict):
            continue
        for sc in params_blob.get("required_status_checks") or []:
            if isinstance(sc, dict) and isinstance(sc.get("context"), str):
                contexts.add(sc["context"])
    missing = [e for e in expected if e not in contexts]
    if missing:
        return False, f"ruleset missing required checks: {', '.join(missing)}"
    return True, "ruleset lists all expected required checks"


# ---------------------------------------------------------------------------
# FetchContext file lookup
# ---------------------------------------------------------------------------


def _resolve_file_text(context: FetchContext, key: Any) -> str | None:
    """Map a YAML ``file:`` key to a pre-fetched text blob on FetchContext."""
    if not isinstance(key, str):
        return None
    mapping = {
        "pipeline.yaml": context.pipeline_text,
        "pipeline": context.pipeline_text,
        "pyproject.toml": context.pyproject_text,
        "pyproject": context.pyproject_text,
        "package.json": context.package_json_text,
        "package": context.package_json_text,
        ".pre-commit-config.yaml": context.precommit_text,
        "precommit": context.precommit_text,
        "renovate": context.renovate_config_text,
        "codeowners": context.codeowners_text,
    }
    return mapping.get(key)


# ---------------------------------------------------------------------------
# Check type dispatch
# ---------------------------------------------------------------------------


@dataclass
class EngineOptions:
    """Runtime options passed from the dashboard into the engine."""

    standards_url: str | None = None
    # Lookup of handlers registered via ``register_handler``. Set at import
    # time in :mod:`_handlers`; the engine reads this dict.
    handlers: dict[str, Any] = field(default_factory=dict)


_BUILTIN_DISPATCH = {
    "file_exists": _check_file_exists,
    "file_absent": _check_file_absent,
    "file_content_matches": _check_file_content_matches,
    "workflow_job_has_step": _check_workflow_job_has_step,
    "workflow_all_jobs_scan": _check_workflow_all_jobs_scan,
    "ruleset_has_required_checks": _check_ruleset_has_required_checks,
}


def _evaluate_one_check(
    context: FetchContext,
    entry: dict,
    options: EngineOptions,
    template_vars: dict[str, str],
    param_override: dict | None = None,
) -> HealthCheckResult:
    """Run a single YAML check entry and produce a result."""
    check_id = str(entry.get("id") or "unknown")
    severity = _parse_severity(entry.get("severity"))
    fail_message = str(entry.get("fail_message") or entry.get("name") or check_id)
    link_tmpl = entry.get("link")
    check_cfg = entry.get("check") or {}
    ctype = check_cfg.get("type")
    params = _format_template(check_cfg.get("params") or check_cfg, template_vars)
    # Strip ``type`` from params when inlined (params == check_cfg).
    if isinstance(params, dict):
        params = {k: v for k, v in params.items() if k != "type"}
    # Per-repo overrides from .github/.ai-compliance.yaml merge on top of the canonical
    # params so repos can supply real URLs / role names without editing the
    # canonical standards.yaml.
    if param_override:
        params = _merge_overrides(params, _format_template(param_override, template_vars))

    try:
        if ctype == "handler":
            handler_name = (params or {}).get("name")
            handler = options.handlers.get(handler_name) if handler_name else None
            if handler is None:
                passed, detail = False, f"handler not registered: {handler_name}"
            else:
                passed, detail = handler(context, params or {})
        elif ctype in _BUILTIN_DISPATCH:
            passed, detail = _BUILTIN_DISPATCH[ctype](context, params or {})
        else:
            passed, detail = False, f"unknown check type: {ctype}"
    except Exception as exc:
        passed, detail = False, f"check error: {exc.__class__.__name__}: {exc}"

    if passed:
        return HealthCheckResult(
            check_name=check_id,
            severity=Severity.OK,
            summary=f"{entry.get('name') or check_id}: {detail}",
        )
    link = None
    if isinstance(link_tmpl, str):
        # Fill in {owner}/{repo_name}/{default_branch} so the URL points at the
        # right repo when the YAML uses templates.
        try:
            link = link_tmpl.format(**template_vars)
        except (KeyError, IndexError):
            link = link_tmpl
    return HealthCheckResult(
        check_name=check_id,
        severity=severity,
        summary=f"{entry.get('name') or check_id}: {detail}"
        if detail
        else f"{entry.get('name') or check_id}: {fail_message}",
        link=link,
    )


# ---------------------------------------------------------------------------
# Engine entry point
# ---------------------------------------------------------------------------


def _repo_applies(entry: dict, repo_tags: set[str]) -> bool:
    applies_to = entry.get("applies_to") or []
    if not isinstance(applies_to, list) or not applies_to:
        return True  # no restriction -> apply everywhere
    return any(tag in repo_tags for tag in applies_to)


def _precondition_met(entry: dict, context: FetchContext) -> tuple[bool, str | None]:
    """Evaluate an optional ``requires_file`` precondition.

    Returns ``(True, None)`` when the precondition passes (or is absent).
    Returns ``(False, reason)`` when the required file is missing, meaning
    the check should auto-pass with "not applicable".
    """
    required = entry.get("requires_file")
    if not required:
        return True, None
    text = _resolve_file_text(context, required)
    if text is not None:
        return True, None
    return False, f"requires {required} (not present)"


_OVERRIDE_META_KEYS = frozenset({"reason", "created_at", "approved_by"})


def _parse_compliance_overrides(
    text: str | None,
) -> tuple[dict[str, str | None], dict[str, dict]]:
    """Parse ``.github/.ai-compliance.yaml`` into ``(disabled_checks, overrides)``.

    ``disabled_checks`` maps each disabled check ID to its reason string
    (or ``None`` when no reason is provided).

    ``overrides`` maps each check ID to its parameter dict with metadata
    keys (``reason``, ``created_at``, ``approved_by``) stripped out.

    ``disabled_checks`` maps each disabled check ID to its reason string
    (or ``None`` when no reason is provided).

    ``overrides`` maps each check ID to its parameter dict with metadata
    keys (``reason``, ``created_at``, ``approved_by``) stripped out.

    Tolerant of absent, empty, or malformed documents -- misconfiguration
    never breaks the refresh loop.
    """
    if not text:
        return {}, {}
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError:
        return {}, {}
    if not isinstance(doc, dict):
        return {}, {}

    # --- disabled_checks ---
    raw_disabled = doc.get("disabled_checks") or []
    disabled: dict[str, str | None] = {}
    if isinstance(raw_disabled, list):
        for entry in raw_disabled:
            if isinstance(entry, dict):
                check_id = entry.get("id")
                if isinstance(check_id, str):
                    disabled[check_id] = entry.get("reason")
            # Bare strings no longer supported -- skip silently.

    # --- overrides ---
    raw_overrides = doc.get("overrides") or {}
    overrides: dict[str, dict] = {}
    if isinstance(raw_overrides, dict):
        for check_id, params in raw_overrides.items():
            if isinstance(check_id, str) and isinstance(params, dict):
                # Strip metadata keys; they are not check parameters.
                overrides[check_id] = {
                    k: v for k, v in params.items() if k not in _OVERRIDE_META_KEYS
                }
    return disabled, overrides


def _merge_overrides(base: Any, override: dict) -> Any:
    """Shallow-merge override keys into the base params mapping."""
    if not isinstance(base, dict):
        return override
    merged = dict(base)
    merged.update(override)
    return merged


def run_engine(
    context: FetchContext,
    options: EngineOptions,
    gh: Github | None,
    repo_tags: set[str],
    default_branch: str,
) -> list[HealthCheckResult]:
    """Fetch standards.yaml (cached) and evaluate every applicable check.

    Returns one ``HealthCheckResult`` per declared check, or a single
    informational result when the document can't be loaded.
    """
    doc = load_standards(gh, options.standards_url)
    if doc is None:
        return [
            HealthCheckResult(
                check_name="standards_engine",
                severity=Severity.OK,
                summary="standards document unavailable (offline or no auth)",
            )
        ]
    checks = doc.get("checks") or []
    if not isinstance(checks, list):
        return [
            HealthCheckResult(
                check_name="standards_engine",
                severity=Severity.MEDIUM,
                summary="standards.yaml: 'checks' must be a list",
            )
        ]
    template_vars: dict[str, str] = {
        "owner": context.owner or "",
        "repo_name": context.repo_name or "",
        "default_branch": default_branch or "main",
    }
    disabled, overrides = _parse_compliance_overrides(context.compliance_overrides_text)
    known_ids = {str(e.get("id")) for e in checks if isinstance(e, dict)}
    stale_opt_outs = set(disabled) - known_ids
    results: list[HealthCheckResult] = []
    for entry in checks:
        if not isinstance(entry, dict):
            continue
        if not _repo_applies(entry, repo_tags):
            continue
        check_id = str(entry.get("id") or "")
        if check_id in disabled:
            # Surface opt-outs as OK-with-reason so coverage reduction stays
            # visible in the dashboard; silent drops are the failure mode we
            # don't want.
            check_name = entry.get("name") or check_id
            reason = disabled[check_id]
            summary = f"{check_name}: disabled by .github/.ai-compliance.yaml"
            if reason:
                truncated = reason[:80] + "..." if len(reason) > 80 else reason
                summary = f"{summary} -- {truncated}"
            results.append(
                HealthCheckResult(
                    check_name=check_id,
                    severity=Severity.OK,
                    summary=summary,
                )
            )
            continue
        met, reason = _precondition_met(entry, context)
        if not met:
            results.append(
                HealthCheckResult(
                    check_name=check_id,
                    severity=Severity.OK,
                    summary=f"{entry.get('name') or check_id}: not applicable ({reason})",
                )
            )
            continue
        results.append(
            _evaluate_one_check(context, entry, options, template_vars, overrides.get(check_id))
        )
    # Surface stale opt-outs as one informational finding so maintainers
    # clean up .github/.ai-compliance.yaml entries that reference retired checks.
    if stale_opt_outs:
        results.append(
            HealthCheckResult(
                check_name="standards_engine.stale_overrides",
                severity=Severity.LOW,
                summary=(
                    ".github/.ai-compliance.yaml references unknown check IDs: "
                    + ", ".join(sorted(stale_opt_outs))
                ),
            )
        )
    return results
