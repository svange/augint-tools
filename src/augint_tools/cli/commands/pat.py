"""Create GitHub fine-grained personal access tokens."""

import asyncio
import sys
from pathlib import Path

import click

from augint_tools.output import CommandResponse, emit_response


def _get_output_opts(ctx: click.Context) -> dict:
    obj = ctx.obj or {}
    return {"json_mode": obj.get("json_mode", False)}


@click.group()
@click.pass_context
def pat(ctx):
    """Manage GitHub fine-grained personal access tokens."""
    ctx.ensure_object(dict)


@pat.command()
@click.option(
    "--repo",
    "repos",
    multiple=True,
    required=True,
    help="Repo scope as owner/repo. May be repeated; all must share the same owner.",
)
@click.option("--name", required=True, help="Token name (must be unique per account).")
@click.option(
    "--permissions",
    required=True,
    help='Comma-separated key=level pairs (e.g. "contents=write,metadata=read").',
)
@click.option(
    "--expires",
    type=click.IntRange(1, 365),
    default=30,
    show_default=True,
    help="Days until expiration (1-365).",
)
@click.option("--description", default="", help="Optional token description.")
@click.option(
    "--env-file",
    type=click.Path(),
    default=None,
    help="Write the token to this .env file instead of printing it.",
)
@click.option(
    "--env-var",
    default="GH_TOKEN",
    show_default=True,
    help="Env var name to use when --env-file is set.",
)
@click.pass_context
def create(ctx, repos, name, permissions, expires, description, env_file, env_var):
    """Create a fine-grained PAT scoped to one or more repos (same owner).

    Credentials are read from GITHUB_USERNAME and GITHUB_PASSWORD env vars;
    missing values are prompted. 2FA TOTP codes are prompted interactively.
    """
    from augint_tools.pat import (
        PatCreationError,
        PatRequest,
        create_pat,
        parse_permissions,
        parse_repo_specs,
        resolve_credentials,
        write_token_to_env,
    )

    opts = _get_output_opts(ctx)

    try:
        owner, repo_names = parse_repo_specs(list(repos))
    except ValueError as exc:
        emit_response(CommandResponse.error("pat create", "repo", str(exc)), **opts)
        sys.exit(1)

    try:
        perms = parse_permissions(permissions)
    except ValueError as exc:
        emit_response(CommandResponse.error("pat create", "repo", str(exc)), **opts)
        sys.exit(1)

    request = PatRequest(
        name=name,
        owner=owner,
        repo_names=repo_names,
        permissions=perms,
        expires_days=expires,
        description=description,
    )

    try:
        credentials = resolve_credentials()
    except PatCreationError as exc:
        emit_response(CommandResponse.error("pat create", "repo", str(exc)), **opts)
        sys.exit(1)

    try:
        token = asyncio.run(create_pat(request, credentials))
    except PatCreationError as exc:
        emit_response(CommandResponse.error("pat create", "repo", str(exc)), **opts)
        sys.exit(1)

    result_data: dict = {
        "name": name,
        "owner": owner,
        "repos": repo_names,
        "permissions": permissions,
        "expires_days": expires,
    }
    next_actions: list[str] = []
    summary = f"Created fine-grained PAT '{name}' for {owner} ({len(repo_names)} repo(s))"

    if env_file:
        try:
            write_token_to_env(Path(env_file), env_var, token)
        except Exception as exc:
            emit_response(
                CommandResponse.error(
                    "pat create",
                    "repo",
                    f"token created but failed to write to {env_file}: {exc}",
                ),
                **opts,
            )
            sys.exit(1)
        result_data["env_file"] = str(env_file)
        result_data["env_var"] = env_var
        summary += f" (stored in {env_file} as {env_var})"
        next_actions.append(f"source {env_file}")
    else:
        if opts["json_mode"]:
            result_data["token"] = token
        else:
            click.echo(token)

    emit_response(
        CommandResponse.ok(
            "pat create",
            "repo",
            summary,
            result=result_data,
            next_actions=next_actions,
        ),
        **opts,
    )
