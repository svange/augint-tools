"""Registered Python handlers for the YAML compliance engine.

Handlers are the escape hatch for checks that can't be expressed as
file-content inspection — anything needing AWS API calls, HTTP probes, or
other out-of-GraphQL data lives here. Each handler has a short, stable name
referenced from ``standards.yaml`` like::

    check:
      type: handler
      name: aws_oidc_trust_policy_scope
      params:
        role_name_template: "{repo_name}-deploy"
        expected_subject_prefix: "repo:{owner}/{repo_name}:"

Contract:

- Each handler is a callable ``(context: FetchContext, params: dict) ->
  tuple[bool, str]`` returning ``(passed, detail_message)``.
- Handlers MUST be side-effect-free, cheap, and tolerant of missing
  credentials: return ``(False, "boto3 not configured: ...")`` rather than
  raising.
- Handlers are discovered via :func:`all_handlers`, which eagerly imports
  this module so every ``@register_handler`` below runs at import time.

Cross-platform: use stdlib + ``requests`` + ``boto3``. Do NOT shell out via
``subprocess`` with ``shell=True`` — Windows-safe means argv lists or
native Python libraries only.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from . import FetchContext


HandlerFn = Callable[["FetchContext", dict], tuple[bool, str]]

_HANDLERS: dict[str, HandlerFn] = {}


def register_handler(name: str) -> Callable[[HandlerFn], HandlerFn]:
    """Decorator that registers a handler under ``name``."""

    def _wrap(fn: HandlerFn) -> HandlerFn:
        _HANDLERS[name] = fn
        return fn

    return _wrap


def all_handlers() -> dict[str, HandlerFn]:
    """Return the handler registry. Eagerly populated at module import."""
    return dict(_HANDLERS)


# ---------------------------------------------------------------------------
# AWS OIDC trust policy scoping
# ---------------------------------------------------------------------------


@register_handler("aws_oidc_trust_policy_scope")
def aws_oidc_trust_policy_scope(context: FetchContext, params: dict) -> tuple[bool, str]:
    """Verify an IAM role's trust policy is scoped to this repo via OIDC.

    Reads the ``AssumeRolePolicyDocument`` of the named role and asserts that
    every ``token.actions.githubusercontent.com:sub`` condition starts with
    ``expected_subject_prefix``. Catches the "role trusts any repo in the org"
    misconfiguration.

    Params:
      role_name_template: e.g. ``"{repo_name}-deploy"``.
      expected_subject_prefix: e.g. ``"repo:{owner}/{repo_name}:"``.
      region: optional; defaults to boto3's env/config chain.
    """
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
    except ImportError:
        return False, "boto3 not installed"

    role_tmpl = params.get("role_name_template") or "{repo_name}-deploy"
    expected_prefix = params.get("expected_subject_prefix") or ""
    region = params.get("region")
    tmpl_vars = {"owner": context.owner or "", "repo_name": context.repo_name or ""}
    role_name = role_tmpl.format(**tmpl_vars)
    expected_prefix = expected_prefix.format(**tmpl_vars)
    if not expected_prefix:
        return False, "expected_subject_prefix is required"

    try:
        iam = boto3.client("iam", region_name=region) if region else boto3.client("iam")
        resp = iam.get_role(RoleName=role_name)
    except NoCredentialsError:
        return False, "AWS credentials not configured"
    except ClientError as exc:
        err_code = exc.response.get("Error", {}).get("Code", "ClientError")
        if err_code == "NoSuchEntity":
            return False, f"role {role_name} not found"
        return False, f"aws: {err_code}"
    except BotoCoreError as exc:
        return False, f"aws: {exc.__class__.__name__}"

    trust_doc = resp.get("Role", {}).get("AssumeRolePolicyDocument")
    if isinstance(trust_doc, str):
        try:
            trust_doc = json.loads(trust_doc)
        except json.JSONDecodeError:
            return False, "trust doc not valid JSON"
    if not isinstance(trust_doc, dict):
        return False, "trust doc missing"

    mismatches: list[str] = []
    found = False
    for stmt in trust_doc.get("Statement") or []:
        if not isinstance(stmt, dict):
            continue
        cond = stmt.get("Condition") or {}
        for op in ("StringLike", "StringEquals"):
            op_block = cond.get(op) or {}
            if not isinstance(op_block, dict):
                continue
            sub = op_block.get("token.actions.githubusercontent.com:sub")
            if sub is None:
                continue
            found = True
            values = sub if isinstance(sub, list) else [sub]
            for v in values:
                if not isinstance(v, str) or not v.startswith(expected_prefix):
                    mismatches.append(v)
    if not found:
        return False, "no OIDC subject condition on trust policy"
    if mismatches:
        return False, f"subject(s) outside prefix: {', '.join(mismatches[:2])}"
    return True, f"role {role_name} scoped to {expected_prefix}*"


# ---------------------------------------------------------------------------
# HTTP health probe
# ---------------------------------------------------------------------------


@register_handler("http_health_probe")
def http_health_probe(context: FetchContext, params: dict) -> tuple[bool, str]:
    """Hit an HTTP URL and assert status code.

    Params:
      url: full URL (may contain ``{owner}``, ``{repo_name}`` templates).
      expected_status: int, default 200.
      timeout_seconds: float, default 5.
    """
    try:
        import requests
    except ImportError:
        return False, "requests not installed"

    url_tmpl = params.get("url") or ""
    expected = int(params.get("expected_status", 200))
    timeout = float(params.get("timeout_seconds", 5.0))
    tmpl_vars = {"owner": context.owner or "", "repo_name": context.repo_name or ""}
    url = url_tmpl.format(**tmpl_vars) if url_tmpl else ""
    if not url:
        return False, "url required"
    try:
        resp = requests.get(url, timeout=timeout, allow_redirects=True)
    except requests.Timeout:
        return False, f"timeout after {timeout}s"
    except requests.RequestException as exc:
        return False, f"http error: {exc.__class__.__name__}"
    if resp.status_code == expected:
        return True, f"GET {url} -> {resp.status_code}"
    return False, f"GET {url} -> {resp.status_code} (expected {expected})"


# ---------------------------------------------------------------------------
# Lambda / CloudFormation deploy provenance
# ---------------------------------------------------------------------------


@register_handler("lambda_deploy_sha_match")
def lambda_deploy_sha_match(context: FetchContext, params: dict) -> tuple[bool, str]:
    """Assert a deployed Lambda's ``git_sha`` tag matches the main-branch head SHA.

    Requires the deploy pipeline to tag resources with ``git_sha`` at deploy
    time (a standard we add alongside this check).

    Params:
      function_name_template: e.g. ``"{repo_name}-prod"``.
      region: optional; defaults to boto3's env/config chain.
      tag_key: default ``"git_sha"``.
    """
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
    except ImportError:
        return False, "boto3 not installed"

    if not context.main_head_sha:
        return False, "main head SHA not available"
    fn_tmpl = params.get("function_name_template") or "{repo_name}-prod"
    region = params.get("region")
    tag_key = params.get("tag_key") or "git_sha"
    tmpl_vars = {"owner": context.owner or "", "repo_name": context.repo_name or ""}
    function_name = fn_tmpl.format(**tmpl_vars)

    try:
        lam = boto3.client("lambda", region_name=region) if region else boto3.client("lambda")
        resp = lam.get_function(FunctionName=function_name)
    except NoCredentialsError:
        return False, "AWS credentials not configured"
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "ClientError")
        if code in ("ResourceNotFoundException", "NoSuchEntity"):
            return False, f"lambda {function_name} not found"
        return False, f"aws: {code}"
    except BotoCoreError as exc:
        return False, f"aws: {exc.__class__.__name__}"

    tags = resp.get("Tags") or {}
    deployed_sha = tags.get(tag_key)
    if not deployed_sha:
        return False, f"{function_name} has no {tag_key} tag"
    if deployed_sha == context.main_head_sha:
        return True, f"{function_name} @ {deployed_sha[:8]}"
    return False, (f"{function_name} at {deployed_sha[:8]} (main is {context.main_head_sha[:8]})")


# Silence the loguru import when unused at import time; keeps the dependency
# visible to linters even if every handler path has ``logger.warning``
# stripped out in future edits.
_ = logger
