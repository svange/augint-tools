"""Auto-classify .env variables as secrets or plain variables.

Detection strategies (any match -> secret, unless safe-value pattern matches first):
1. Safe-value patterns: ARNs, URLs, bucket names, short slugs -> variable.
2. Key-name keywords: token, secret, key, password, etc.
3. Known value prefixes: ghp_, sk-, AKIA, xox-, etc.
4. Shannon entropy: high-entropy values suggest random secrets.
5. Structural patterns: JWTs, long hex strings, base64 blobs.

Override mechanisms:
- Inline .env comments: ``# @var`` or ``# @secret`` on the preceding line,
  or trailing ``# var`` / ``# secret`` on the same line.
- Programmatic overrides via ``force_var`` / ``force_secret`` sets passed to
  ``classify_variable``, ``classify_env``, and ``partition_env``.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from dotenv import dotenv_values
from loguru import logger


class Classification(Enum):
    SECRET = "secret"
    VARIABLE = "variable"
    SKIP = "skip"


KEY_SKIP_PREFIXES = frozenset({"AWS_PROFILE", "AWS_DEFAULT_REGION", "AWS_REGION"})

KEY_SECRET_KEYWORDS = frozenset(
    {
        "secret",
        "key",
        "token",
        "bearer",
        "password",
        "pass",
        "pwd",
        "pword",
        "hash",
        "credential",
        "private",
        "auth",
        "apikey",
        "api_key",
        "signing",
    }
)

VALUE_SECRET_PREFIXES = (
    "ghp_",
    "gho_",
    "ghu_",
    "ghs_",
    "ghr_",
    "github_pat_",
    "sk-",
    "sk_live_",
    "sk_test_",
    "pk_live_",
    "pk_test_",
    "xox",
    "AKIA",
    "whsec_",
    "rk_live_",
    "rk_test_",
    "shpat_",
    "shpss_",
    "shpca_",
    "sqOatp-",
    "eyJ",
)

_HEX_RE = re.compile(r"^[0-9a-fA-F]{32,}$")
_BASE64_RE = re.compile(r"^[A-Za-z0-9+/]{20,}={0,3}$")
_JWT_RE = re.compile(r"^eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")

# Safe-value patterns: values matching these are infrastructure identifiers, not secrets.
_ARN_RE = re.compile(r"^arn:aws[a-zA-Z-]*:[a-zA-Z0-9-]+:[a-zA-Z0-9-]*:\d{0,12}:")
_URL_RE = re.compile(r"^https?://")

# Key-name suffixes that indicate infrastructure identifiers (not secrets),
# even when the key also contains a secret keyword like "role" containing "key" etc.
_INFRA_KEY_SUFFIXES = (
    "_role",
    "_bucket",
    "_distribution",
    "_zone",
    "_cert_arn",
    "_certificate_arn",
    "_origins",
    "_repo",
    "_project",
    "_name",
    "_region",
    "_domain",
    "_host",
    "_port",
    "_url",
    "_uri",
    "_endpoint",
    "_account",
    "_stack",
)

ENTROPY_THRESHOLD = 3.5
ENTROPY_MIN_LENGTH = 12


def _shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    freq: dict[str, int] = {}
    for ch in value:
        freq[ch] = freq.get(ch, 0) + 1
    length = len(value)
    return -sum((c / length) * math.log2(c / length) for c in freq.values())


def _is_safe_value(value: str) -> str | None:
    """Return a reason string if *value* looks like a non-secret infrastructure identifier.

    Only matches patterns that are unambiguously non-secret: AWS ARNs and URLs.
    Short slugs and bucket names are handled by ``_is_infra_key`` (key-suffix check)
    rather than by value inspection, to avoid false negatives on actual secrets.

    Returns ``None`` when no safe pattern matches.
    """
    if _ARN_RE.match(value):
        return "AWS ARN"
    if _URL_RE.match(value):
        return "URL"
    return None


def _is_infra_key(key: str) -> str | None:
    """Return a reason string if *key* ends with a known infrastructure suffix.

    Returns ``None`` when no suffix matches.
    """
    lower = key.casefold()
    for suffix in _INFRA_KEY_SUFFIXES:
        if lower.endswith(suffix):
            return f"key suffix '{suffix}'"
    return None


def _parse_env_comments(filename: str) -> dict[str, str]:
    """Scan an .env file for inline classification hints.

    Supported formats:
    - Preceding-line hint: ``# @var`` or ``# @secret`` on the line immediately
      before a ``KEY=value`` line.
    - Trailing hint: ``KEY=value # var`` or ``KEY=value # secret`` on the same line.

    Returns a dict mapping key names to ``"var"`` or ``"secret"``.
    """
    hints: dict[str, str] = {}
    path = Path(filename)
    if not path.exists():
        return hints

    prev_hint: str | None = None
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()

        # Check for preceding-line comment hint
        if line.startswith("#"):
            lower = line.casefold()
            if "@var" in lower:
                prev_hint = "var"
            elif "@secret" in lower:
                prev_hint = "secret"
            else:
                prev_hint = None
            continue

        # Try to extract KEY from a KEY=value line
        if "=" not in line:
            prev_hint = None
            continue

        # Handle trailing comment hint: KEY=value # var  /  KEY=value # secret
        key_part = line.split("=", 1)[0].strip()
        value_and_comment = line.split("=", 1)[1] if "=" in line else ""

        # Check trailing comment (after the value)
        trailing_hint: str | None = None
        if "#" in value_and_comment:
            comment_part = value_and_comment.rsplit("#", 1)[1].strip().casefold()
            if comment_part == "var":
                trailing_hint = "var"
            elif comment_part == "secret":
                trailing_hint = "secret"

        # Trailing hint takes precedence over preceding-line hint
        if trailing_hint:
            hints[key_part] = trailing_hint
        elif prev_hint:
            hints[key_part] = prev_hint

        prev_hint = None

    return hints


@dataclass
class ClassificationResult:
    key: str
    value: str
    classification: Classification
    reasons: list[str] = field(default_factory=list)


def classify_variable(
    key: str,
    value: str,
    *,
    force_var: frozenset[str] | set[str] | None = None,
    force_secret: frozenset[str] | set[str] | None = None,
    comment_hint: str | None = None,
) -> ClassificationResult:
    """Classify a single key/value pair.

    Parameters
    ----------
    key:
        Environment variable name.
    value:
        Environment variable value.
    force_var:
        Set of key names that must be classified as variables.
    force_secret:
        Set of key names that must be classified as secrets.
    comment_hint:
        ``"var"`` or ``"secret"`` parsed from an inline .env comment.
    """
    # --- Skip-list check (unchanged) ---
    if key in KEY_SKIP_PREFIXES:
        logger.debug(f"[classify] {key} -> SKIP (skip-list key)")
        return ClassificationResult(key, value, Classification.SKIP, ["skip-list key"])

    if not value:
        logger.debug(f"[classify] {key} -> SKIP (empty value)")
        return ClassificationResult(key, value, Classification.SKIP, ["empty value"])

    # --- Explicit overrides (highest priority) ---
    _force_var = force_var or set()
    _force_secret = force_secret or set()

    if key in _force_secret:
        logger.info(f"[classify] {key} -> SECRET (--force-secret override)")
        return ClassificationResult(key, value, Classification.SECRET, ["--force-secret override"])
    if key in _force_var:
        logger.info(f"[classify] {key} -> VARIABLE (--force-var override)")
        return ClassificationResult(key, value, Classification.VARIABLE, ["--force-var override"])

    # --- Comment hint overrides (second priority) ---
    if comment_hint == "secret":
        logger.info(f"[classify] {key} -> SECRET (inline comment hint @secret)")
        return ClassificationResult(
            key, value, Classification.SECRET, ["inline comment hint @secret"]
        )
    if comment_hint == "var":
        logger.info(f"[classify] {key} -> VARIABLE (inline comment hint @var)")
        return ClassificationResult(
            key, value, Classification.VARIABLE, ["inline comment hint @var"]
        )

    # --- Heuristic classification ---
    reasons: list[str] = []

    # Determine whether the key has an infrastructure suffix (e.g. _role, _bucket, _url).
    # When it does, weak secret signals (key-keyword, entropy) are suppressed.
    infra_reason = _is_infra_key(key)

    # Check key-name keywords (potential secret signal)
    lower_key = key.casefold()
    for kw in KEY_SECRET_KEYWORDS:
        if kw in lower_key:
            if infra_reason:
                logger.debug(f"[classify] {key}: keyword '{kw}' suppressed by {infra_reason}")
            else:
                reasons.append(f"key contains '{kw}'")
            break

    # Check value prefixes for known secret formats (strong signal)
    for prefix in VALUE_SECRET_PREFIXES:
        if value.startswith(prefix):
            reasons.append(f"value prefix '{prefix}'")
            break

    # Check structural patterns (strong signals)
    if _JWT_RE.match(value):
        reasons.append("JWT pattern")
    elif _HEX_RE.match(value):
        reasons.append("long hex string")
    elif _BASE64_RE.match(value) and len(value) >= 24:
        reasons.append("base64 blob")

    # Check entropy (weak signal -- suppressed when key has infra suffix)
    if len(value) >= ENTROPY_MIN_LENGTH:
        entropy = _shannon_entropy(value)
        if entropy >= ENTROPY_THRESHOLD:
            if infra_reason:
                logger.debug(
                    f"[classify] {key}: high entropy ({entropy:.2f}) suppressed by {infra_reason}"
                )
            else:
                reasons.append(f"high entropy ({entropy:.2f})")

    # If heuristic flagged it as secret, check safe-value patterns as a final gate.
    # Safe values (ARNs, URLs) suppress weak secret signals (key-keyword, entropy)
    # but NOT strong signals (value-prefix, JWT, hex, base64).
    if reasons:
        safe_reason = _is_safe_value(value)
        if safe_reason:
            strong_signals = {
                r
                for r in reasons
                if r.startswith("value prefix")
                or r.startswith("JWT")
                or r.startswith("long hex")
                or r.startswith("base64")
            }
            if not strong_signals:
                logger.debug(
                    f"[classify] {key}: secret reasons {reasons} "
                    f"suppressed by safe value pattern ({safe_reason})"
                )
                reasons.clear()
                reasons.append(f"safe value: {safe_reason}")

    # Secret iff we have reasons AND none are safe-value overrides
    has_safe = any(r.startswith("safe value:") for r in reasons)
    if reasons and not has_safe:
        classification = Classification.SECRET
    else:
        classification = Classification.VARIABLE

    logger.debug(f"[classify] {key} -> {classification.value} (reasons: {reasons})")
    return ClassificationResult(key, value, classification, reasons)


def classify_env(
    filename: str = ".env",
    *,
    force_var: frozenset[str] | set[str] | None = None,
    force_secret: frozenset[str] | set[str] | None = None,
) -> list[ClassificationResult]:
    """Read an env file and classify every variable.

    Parameters
    ----------
    filename:
        Path to the .env file.
    force_var:
        Set of key names forced to VARIABLE regardless of heuristic.
    force_secret:
        Set of key names forced to SECRET regardless of heuristic.
    """
    path = Path(filename)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    comment_hints = _parse_env_comments(filename)
    values = dotenv_values(str(path))
    results: list[ClassificationResult] = []
    for key, value in values.items():
        if value is None:
            continue
        results.append(
            classify_variable(
                key,
                value,
                force_var=force_var,
                force_secret=force_secret,
                comment_hint=comment_hints.get(key),
            )
        )
    return results


def partition_env(
    filename: str = ".env",
    *,
    force_var: frozenset[str] | set[str] | None = None,
    force_secret: frozenset[str] | set[str] | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Partition env file into (secrets, variables) dicts, skipping skip-listed keys."""
    results = classify_env(filename, force_var=force_var, force_secret=force_secret)
    secrets: dict[str, str] = {}
    variables: dict[str, str] = {}
    for r in results:
        if r.classification == Classification.SECRET:
            secrets[r.key] = r.value
        elif r.classification == Classification.VARIABLE:
            variables[r.key] = r.value
    return secrets, variables
