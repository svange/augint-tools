"""Standardization workflow commands: detect, audit, fix, verify."""

import sys
from pathlib import Path

import click

from augint_tools.detection import detect
from augint_tools.git import is_git_repo
from augint_tools.output import CommandResponse, emit_response
from augint_tools.standardize.audit import run_audit
from augint_tools.standardize.fix import apply_fixes
from augint_tools.standardize.verify import verify as verify_audit


def _get_output_opts(ctx: click.Context) -> dict:
    obj = ctx.obj or {}
    return {
        "json_mode": obj.get("json_mode", False),
        "actionable": obj.get("actionable", False),
        "summary_only": obj.get("summary_only", False),
    }


def _resolve_profile_id(context) -> str:
    """Build a profile identifier from detection context."""
    parts = [context.language]
    if context.repo_kind:
        parts.append(context.repo_kind)
    if context.framework != "plain":
        parts.append(context.framework)
    return "-".join(parts)


def _parse_sections(section_str: str | None) -> list[str] | None:
    """Parse --section flag into a list, or None for all."""
    if not section_str or section_str == "all":
        return None
    return [s.strip() for s in section_str.split(",")]


# --- Top-level group ---


@click.group()
@click.pass_context
def standardize(ctx):
    """Standardization workflow: detect, audit, fix, verify."""
    ctx.ensure_object(dict)


# --- standardize detect ---


@standardize.command("detect")
@click.pass_context
def detect_cmd(ctx):
    """Resolve the standardization profile for this repo."""
    cwd = Path.cwd()
    opts = _get_output_opts(ctx)

    if not is_git_repo(cwd):
        emit_response(
            CommandResponse.error("standardize detect", "standardize", "Not in a git repository"),
            **opts,
        )
        sys.exit(1)

    context = detect(cwd)
    profile_id = _resolve_profile_id(context)

    emit_response(
        CommandResponse.ok(
            "standardize detect",
            "standardize",
            f"Profile: {profile_id}",
            result={
                "profile_id": profile_id,
                "repo_kind": context.repo_kind,
                "language": context.language,
                "framework": context.framework,
                "branch_strategy": context.branch_strategy,
                "default_branch": context.default_branch,
            },
        ),
        **opts,
    )


# --- standardize audit ---


@standardize.command()
@click.option("--section", help="Comma-separated sections to check (or 'all').")
@click.pass_context
def audit(ctx, section):
    """Run standardization checks through a normalized finding model."""
    cwd = Path.cwd()
    opts = _get_output_opts(ctx)

    if not is_git_repo(cwd):
        emit_response(
            CommandResponse.error("standardize audit", "standardize", "Not in a git repository"),
            **opts,
        )
        sys.exit(1)

    context = detect(cwd)
    profile_id = _resolve_profile_id(context)
    sections = _parse_sections(section)

    result = run_audit(cwd, context, profile_id, sections=sections)

    status = (
        "ok"
        if result.summary.total_errors == 0 and result.summary.total_warnings == 0
        else "action-required"
    )
    summary = f"{result.summary.total_errors} errors, {result.summary.total_warnings} warnings across {result.summary.sections_checked} sections"

    next_actions = []
    if result.summary.fixable_count > 0:
        next_actions.append(f"run standardize fix ({result.summary.fixable_count} auto-fixable)")

    emit_response(
        CommandResponse(
            command="standardize audit",
            scope="standardize",
            status=status,
            summary=summary,
            result=result.to_dict(),
            next_actions=next_actions,
        ),
        **opts,
    )
    if result.summary.total_errors > 0:
        sys.exit(2)


# --- standardize fix ---


@standardize.command()
@click.option("--section", help="Comma-separated sections to fix (or 'all').")
@click.option("--dry-run", is_flag=True, default=True, help="Plan fixes without writing (default).")
@click.option("--write", is_flag=True, default=False, help="Apply fixes to disk.")
@click.pass_context
def fix(ctx, section, dry_run, write):
    """Apply template-backed or rule-backed fixes from audit findings."""
    cwd = Path.cwd()
    opts = _get_output_opts(ctx)

    if not is_git_repo(cwd):
        emit_response(
            CommandResponse.error("standardize fix", "standardize", "Not in a git repository"),
            **opts,
        )
        sys.exit(1)

    context = detect(cwd)
    profile_id = _resolve_profile_id(context)
    sections = _parse_sections(section)

    # Run audit first to get findings
    audit_result = run_audit(cwd, context, profile_id, sections=sections)

    # Apply fixes
    actually_write = write and not dry_run if dry_run else write
    # If --write is passed, always write even if --dry-run default is True
    if write:
        actually_write = True

    fix_result = apply_fixes(
        cwd, audit_result, dry_run=not actually_write, sections=sections if sections else None
    )

    if actually_write:
        summary = (
            f"{len(fix_result.actions_applied)} applied, {len(fix_result.actions_failed)} failed"
        )
        next_actions = ["run standardize verify"] if fix_result.actions_applied else []
    else:
        summary = f"{len(fix_result.actions_planned)} fixes planned (dry-run)"
        next_actions = ["run with --write to apply"] if fix_result.actions_planned else []

    emit_response(
        CommandResponse.ok(
            "standardize fix",
            "standardize",
            summary,
            result=fix_result.to_dict(),
            next_actions=next_actions,
        ),
        **opts,
    )


# --- standardize verify ---


@standardize.command()
@click.option("--section", help="Comma-separated sections to verify (or 'all').")
@click.pass_context
def verify(ctx, section):
    """Re-run audit after fixes to confirm alignment."""
    cwd = Path.cwd()
    opts = _get_output_opts(ctx)

    if not is_git_repo(cwd):
        emit_response(
            CommandResponse.error("standardize verify", "standardize", "Not in a git repository"),
            **opts,
        )
        sys.exit(1)

    context = detect(cwd)
    profile_id = _resolve_profile_id(context)
    sections = _parse_sections(section)

    result = verify_audit(cwd, context, profile_id, sections=sections)

    remaining = result.summary.total_errors + result.summary.total_warnings
    if remaining == 0:
        status = "ok"
        summary = "All sections aligned"
    else:
        status = "action-required"
        summary = f"{remaining} issues remain ({result.summary.total_errors} errors, {result.summary.total_warnings} warnings)"

    emit_response(
        CommandResponse(
            command="standardize verify",
            scope="standardize",
            status=status,
            summary=summary,
            result=result.to_dict(),
        ),
        **opts,
    )
    if result.summary.total_errors > 0:
        sys.exit(2)
