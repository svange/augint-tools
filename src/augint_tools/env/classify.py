"""Auto-classify .env variables as secrets or plain variables.

Detection strategies (any match -> secret):
1. Key-name keywords: token, secret, key, password, etc.
2. Known value prefixes: ghp_, sk-, AKIA, xox-, etc.
3. Shannon entropy: high-entropy values suggest random secrets.
4. Structural patterns: JWTs, long hex strings, base64 blobs.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from dotenv import dotenv_values


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


@dataclass
class ClassificationResult:
    key: str
    value: str
    classification: Classification
    reasons: list[str] = field(default_factory=list)


def classify_variable(key: str, value: str) -> ClassificationResult:
    """Classify a single key/value pair."""
    reasons: list[str] = []

    if key in KEY_SKIP_PREFIXES:
        return ClassificationResult(key, value, Classification.SKIP, ["skip-list key"])

    if not value:
        return ClassificationResult(key, value, Classification.SKIP, ["empty value"])

    lower_key = key.casefold()
    for kw in KEY_SECRET_KEYWORDS:
        if kw in lower_key:
            reasons.append(f"key contains '{kw}'")
            break

    for prefix in VALUE_SECRET_PREFIXES:
        if value.startswith(prefix):
            reasons.append(f"value prefix '{prefix}'")
            break

    if _JWT_RE.match(value):
        reasons.append("JWT pattern")
    elif _HEX_RE.match(value):
        reasons.append("long hex string")
    elif _BASE64_RE.match(value) and len(value) >= 24:
        reasons.append("base64 blob")

    if len(value) >= ENTROPY_MIN_LENGTH:
        entropy = _shannon_entropy(value)
        if entropy >= ENTROPY_THRESHOLD:
            reasons.append(f"high entropy ({entropy:.2f})")

    classification = Classification.SECRET if reasons else Classification.VARIABLE
    return ClassificationResult(key, value, classification, reasons)


def classify_env(filename: str = ".env") -> list[ClassificationResult]:
    """Read an env file and classify every variable."""
    path = Path(filename)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    values = dotenv_values(str(path))
    results: list[ClassificationResult] = []
    for key, value in values.items():
        if value is None:
            continue
        results.append(classify_variable(key, value))
    return results


def partition_env(
    filename: str = ".env",
) -> tuple[dict[str, str], dict[str, str]]:
    """Partition env file into (secrets, variables) dicts, skipping skip-listed keys."""
    results = classify_env(filename)
    secrets: dict[str, str] = {}
    variables: dict[str, str] = {}
    for r in results:
        if r.classification == Classification.SECRET:
            secrets[r.key] = r.value
        elif r.classification == Classification.VARIABLE:
            variables[r.key] = r.value
    return secrets, variables
