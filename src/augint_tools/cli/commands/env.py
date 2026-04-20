"""GitHub and environment variable management commands."""

import asyncio
import sys

import click

from augint_tools.output import CommandResponse, emit_response


def _get_output_opts(ctx: click.Context) -> dict:
    obj = ctx.obj or {}
    return {"json_mode": obj.get("json_mode", False)}


@click.group()
@click.pass_context
def gh(ctx):
    """GitHub secrets and variable management."""
    ctx.ensure_object(dict)


@gh.command()
@click.option(
    "--force-var",
    default="",
    help="Comma-separated key names to force-classify as variables.",
)
@click.option(
    "--force-secret",
    default="",
    help="Comma-separated key names to force-classify as secrets.",
)
@click.argument("filename", type=click.Path(), default=".env")
@click.pass_context
def classify(ctx, force_var, force_secret, filename):
    """Show how each .env variable would be classified (secret vs variable)."""
    from augint_tools.env.classify import Classification, classify_env

    opts = _get_output_opts(ctx)

    fv = frozenset(k.strip() for k in force_var.split(",") if k.strip()) if force_var else None
    fs = (
        frozenset(k.strip() for k in force_secret.split(",") if k.strip()) if force_secret else None
    )

    try:
        results = classify_env(filename, force_var=fv, force_secret=fs)
    except FileNotFoundError as e:
        emit_response(CommandResponse.error("gh classify", "repo", str(e)), **opts)
        sys.exit(1)

    secrets = [r for r in results if r.classification == Classification.SECRET]
    variables = [r for r in results if r.classification == Classification.VARIABLE]
    skipped = [r for r in results if r.classification == Classification.SKIP]

    result_data = {
        "secrets": [{"key": r.key, "reasons": r.reasons} for r in secrets],
        "variables": [{"key": r.key, "reasons": r.reasons} for r in variables],
        "skipped": [r.key for r in skipped],
    }

    summary = f"{len(secrets)} secrets, {len(variables)} variables, {len(skipped)} skipped"

    emit_response(
        CommandResponse.ok("gh classify", "repo", summary, result=result_data),
        **opts,
    )


@gh.command()
@click.option(
    "--dry-run", "-d", is_flag=True, help="Show what would change without modifying GitHub."
)
@click.option("--verbose", "-v", is_flag=True, help="Print detailed output.")
@click.option(
    "--force-var",
    default="",
    help="Comma-separated key names to force-classify as variables.",
)
@click.option(
    "--force-secret",
    default="",
    help="Comma-separated key names to force-classify as secrets.",
)
@click.argument("filename", type=click.Path(exists=True), default=".env")
@click.pass_context
def push(ctx, dry_run, verbose, force_var, force_secret, filename):
    """Push .env secrets and variables to GitHub repository settings."""
    from augint_tools.env.sync import perform_sync

    opts = _get_output_opts(ctx)

    fv = frozenset(k.strip() for k in force_var.split(",") if k.strip()) if force_var else None
    fs = (
        frozenset(k.strip() for k in force_secret.split(",") if k.strip()) if force_secret else None
    )

    try:
        results = asyncio.run(perform_sync(filename, dry_run, force_var=fv, force_secret=fs))
    except Exception as e:
        emit_response(CommandResponse.error("gh push", "repo", str(e)), **opts)
        sys.exit(1)

    n_secrets = len(results["secrets"])
    n_vars = len(results["variables"])
    prefix = "[DRY RUN] " if dry_run else ""
    summary = f"{prefix}Synced {n_secrets} secrets and {n_vars} variables"

    emit_response(
        CommandResponse.ok(
            "gh push",
            "repo",
            summary,
            result={
                "secrets": results["secrets"],
                "variables": results["variables"],
                "dry_run": dry_run,
            },
            next_actions=["verify in GitHub Settings > Secrets and variables > Actions"],
        ),
        **opts,
    )


@click.command()
@click.option("--no-sync", is_flag=True, help="Skip pushing secrets to GitHub.")
@click.option("--verbose", "-v", is_flag=True, help="Print detailed output.")
@click.option(
    "--dry-run", "-d", is_flag=True, help="Show what would be done without making changes."
)
@click.argument("filename", type=click.Path(), default=".env")
@click.pass_context
def sync(ctx, no_sync, verbose, dry_run, filename):
    """Back up .env to chezmoi and sync secrets to GitHub."""
    from augint_tools.env.chezmoi import chezmoi_backup

    opts = _get_output_opts(ctx)

    try:
        result = chezmoi_backup(
            filename,
            sync_github=not no_sync,
            verbose=verbose,
            dry_run=dry_run,
        )
    except click.ClickException as e:
        emit_response(CommandResponse.error("sync", "repo", e.format_message()), **opts)
        sys.exit(1)
    except Exception as e:
        emit_response(CommandResponse.error("sync", "repo", str(e)), **opts)
        sys.exit(1)

    parts = []
    if result.get("chezmoi_committed"):
        parts.append("chezmoi backup complete")
    else:
        parts.append("no chezmoi changes")

    s = result.get("secrets_synced", 0)
    v = result.get("variables_synced", 0)
    if not no_sync:
        parts.append(f"{s} secrets, {v} variables synced")

    prefix = "[DRY RUN] " if dry_run else ""
    summary = f"{prefix}{'; '.join(parts)}"

    emit_response(
        CommandResponse.ok("sync", "repo", summary, result=result),
        **opts,
    )
