"""CLI commands for team shared secrets management."""

import asyncio
import sys

import click

from augint_tools.output import CommandResponse, emit_response


def _get_output_opts(ctx: click.Context) -> dict:
    obj = ctx.obj or {}
    return {"json_mode": obj.get("json_mode", False)}


# ---------------------------------------------------------------------------
# Top-level group: ai-tools team-secrets <team> ...
# ---------------------------------------------------------------------------


@click.group("team-secrets")
@click.argument("team")
@click.pass_context
def team_secrets_group(ctx, team):
    """Team shared secrets management (SOPS + age)."""
    ctx.ensure_object(dict)
    ctx.obj["team"] = team


# ---------------------------------------------------------------------------
# Primary commands
# ---------------------------------------------------------------------------


@team_secrets_group.command()
@click.option("--repo", "repo_path", default=None, help="Path to team secrets repo.")
@click.option("--project", default=None, help="Optionally initialize a project during setup.")
@click.option("--env", "env_name", default="dev", help="Environment to verify (default: dev).")
@click.pass_context
def setup(ctx, repo_path, project, env_name):
    """Guided first-time bootstrap for a team."""
    from augint_tools.team_secrets.age import is_age_installed
    from augint_tools.team_secrets.keys import (
        bootstrap_key,
        get_cached_key,
        resolve_github_username,
        resolve_repo_path,
        save_team_config,
    )
    from augint_tools.team_secrets.models import TeamConfig
    from augint_tools.team_secrets.repo import init_project, is_team_repo, list_projects
    from augint_tools.team_secrets.sops import decrypt_file, is_sops_installed

    opts = _get_output_opts(ctx)
    team = ctx.obj["team"]

    # Step 1: Check tools
    if not is_sops_installed():
        emit_response(
            CommandResponse.error(
                "team-secrets setup",
                "team",
                "sops >= 3.8 not found. Install: https://github.com/getsops/sops/releases",
            ),
            **opts,
        )
        sys.exit(1)

    if not is_age_installed():
        emit_response(
            CommandResponse.error(
                "team-secrets setup",
                "team",
                "age not found. Install: https://github.com/FiloSottile/age/releases",
            ),
            **opts,
        )
        sys.exit(1)

    click.echo("Prerequisites OK (sops, age installed)")

    # Step 2: Resolve GitHub username
    username = resolve_github_username()
    if not username:
        username = click.prompt("GitHub username (could not detect via gh CLI)")
    click.echo(f"Username: {username}")

    # Step 3: Locate team repo
    resolved_path = resolve_repo_path(team, repo_path)
    if resolved_path is None:
        if sys.stdin.isatty():
            resolved_path_str = click.prompt(
                f"Path to {team}-secrets repo",
                type=click.Path(),
            )
            from pathlib import Path

            resolved_path = Path(resolved_path_str).expanduser().resolve()
        else:
            emit_response(
                CommandResponse.error(
                    "team-secrets setup",
                    "team",
                    "Cannot locate team repo. Pass --repo or set up ~/.augint-tools/teams.yaml",
                ),
                **opts,
            )
            sys.exit(1)

    if not is_team_repo(resolved_path):
        click.echo(f"Warning: {resolved_path} does not look like a team secrets repo")
        if sys.stdin.isatty():
            if not click.confirm("Continue anyway?"):
                sys.exit(0)

    click.echo(f"Team repo: {resolved_path}")

    # Step 4: Bootstrap key
    from pathlib import Path

    key_path = get_cached_key(team)
    if key_path:
        click.echo(f"Key already cached at {key_path}")
    else:
        encrypted_key_file = resolved_path / "keys" / f"{username}.key.enc"
        if encrypted_key_file.exists():
            password = click.prompt("Enter password for your encrypted key", hide_input=True)
            try:
                key_path = bootstrap_key(team, resolved_path, username, password)
                click.echo(f"Key decrypted and cached at {key_path}")
            except RuntimeError as e:
                emit_response(
                    CommandResponse.error("team-secrets setup", "team", f"Key decrypt failed: {e}"),
                    **opts,
                )
                sys.exit(1)
        else:
            click.echo(
                f"No encrypted key found at {encrypted_key_file}.\n"
                f"Ask an admin to run: ai-tools team-secrets {team} admin add-user {username}"
            )
            emit_response(
                CommandResponse.error("team-secrets setup", "team", "No encrypted key for user"),
                **opts,
            )
            sys.exit(1)

    # Step 5: Save config
    config = TeamConfig(name=team, repo_path=resolved_path, username=username)
    save_team_config(config)
    click.echo("Team config saved")

    # Step 6: Verify decryption
    if key_path:
        projects = list_projects(resolved_path)
        verified = False
        for p in projects:
            enc_file = resolved_path / "projects" / p / f"{env_name}.enc.env"
            if enc_file.exists():
                try:
                    decrypt_file(enc_file, key_path)
                    click.echo(f"Verified: can decrypt {p}/{env_name}.enc.env")
                    verified = True
                    break
                except RuntimeError:
                    continue
        if not verified and projects:
            click.echo("Warning: could not verify decryption on any project file")

    # Step 7: Optionally init project
    if project:
        init_project(resolved_path, project, team)
        click.echo(f"Initialized project '{project}'")

    emit_response(
        CommandResponse.ok(
            "team-secrets setup",
            "team",
            f"Setup complete for team '{team}'",
            result={"repo_path": str(resolved_path), "username": username},
            next_actions=[
                f"ai-tools team-secrets {team} doctor",
                f"ai-tools team-secrets {team} edit <project> --env dev",
            ],
        ),
        **opts,
    )


@team_secrets_group.command()
@click.pass_context
def doctor(ctx):
    """Verify team secrets health (tools, keys, access)."""
    from augint_tools.team_secrets.doctor import run_checks

    opts = _get_output_opts(ctx)
    team = ctx.obj["team"]

    checks = run_checks(team)

    # Display results
    if not opts["json_mode"]:
        click.echo(f"\nTeam '{team}' health check:")
        click.echo("-" * 50)
        for check in checks:
            icon = {"pass": "+", "warn": "~", "fail": "x"}[check.status]
            click.echo(f"  [{icon}] {check.name}: {check.message}")
        click.echo("")

    failures = [c for c in checks if c.status == "fail"]
    warnings = [c for c in checks if c.status == "warn"]

    status = "ok" if not failures else "action-required"
    summary = f"{len(checks)} checks: {len(checks) - len(failures) - len(warnings)} pass, {len(warnings)} warn, {len(failures)} fail"

    emit_response(
        CommandResponse(
            command="team-secrets doctor",
            scope="team",
            status=status,
            summary=summary,
            result={
                "checks": [
                    {"name": c.name, "status": c.status, "message": c.message} for c in checks
                ]
            },
            next_actions=[f"Fix: {c.message}" for c in failures[:3]],
            warnings=[c.message for c in warnings],
            errors=[c.message for c in failures],
        ),
        **opts,
    )
    if failures:
        sys.exit(2)


@team_secrets_group.command()
@click.argument("project")
@click.option("--env", "env_name", default="dev", help="Environment (default: dev).")
@click.pass_context
def edit(ctx, project, env_name):
    """Edit encrypted secrets for a project in $EDITOR."""
    from augint_tools.team_secrets.keys import load_team_config, require_key
    from augint_tools.team_secrets.repo import get_encrypted_env_path
    from augint_tools.team_secrets.sops import edit_file

    opts = _get_output_opts(ctx)
    team = ctx.obj["team"]

    key_path = require_key(team)
    config = load_team_config(team)
    if not config:
        emit_response(
            CommandResponse.error(
                "team-secrets edit",
                "team",
                f"No config for team '{team}'. Run: ai-tools team-secrets {team} setup",
            ),
            **opts,
        )
        sys.exit(1)

    encrypted_path = get_encrypted_env_path(config.repo_path, project, env_name)
    if not encrypted_path.exists():
        emit_response(
            CommandResponse.error(
                "team-secrets edit",
                "team",
                f"No file at {encrypted_path}. "
                f"Run: ai-tools team-secrets {team} admin init-project {project}",
            ),
            **opts,
        )
        sys.exit(1)

    try:
        edit_file(encrypted_path, key_path)
    except RuntimeError as e:
        emit_response(CommandResponse.error("team-secrets edit", "team", str(e)), **opts)
        sys.exit(1)

    emit_response(
        CommandResponse.ok(
            "team-secrets edit",
            "team",
            f"Edited {project}/{env_name}.enc.env",
            result={"project": project, "env": env_name},
        ),
        **opts,
    )


@team_secrets_group.command("sync")
@click.argument("project")
@click.option("--env", "env_name", default="dev", help="Environment (default: dev).")
@click.option("--dry-run", is_flag=True, help="Show what would change without modifying anything.")
@click.option("--diff", "diff_only", is_flag=True, help="Show diff and exit.")
@click.option("--no-gh", is_flag=True, help="Skip pushing to GitHub secrets/variables.")
@click.option("--no-commit", is_flag=True, help="Skip committing to team repo.")
@click.option("--no-push", is_flag=True, help="Skip pushing team repo.")
@click.option("--write-local-env", is_flag=True, help="Write merged result to local .env.")
@click.pass_context
def sync_cmd(
    ctx, project, env_name, dry_run, diff_only, no_gh, no_commit, no_push, write_local_env
):
    """Merge local .env with team secrets and distribute."""
    from augint_tools.team_secrets.keys import load_team_config, require_key
    from augint_tools.team_secrets.repo import pull_repo
    from augint_tools.team_secrets.sync import perform_team_sync

    opts = _get_output_opts(ctx)
    team = ctx.obj["team"]

    key_path = require_key(team)
    config = load_team_config(team)
    if not config:
        emit_response(
            CommandResponse.error(
                "team-secrets sync",
                "team",
                f"No config for team '{team}'. Run: ai-tools team-secrets {team} setup",
            ),
            **opts,
        )
        sys.exit(1)

    # Pull latest
    if not dry_run and not diff_only:
        pull_repo(config.repo_path)

    try:
        result = perform_team_sync(
            team_repo_path=config.repo_path,
            project=project,
            env=env_name,
            key_file=key_path,
            dry_run=dry_run,
            diff_only=diff_only,
            write_local_env=write_local_env,
            no_commit=no_commit,
            no_push=no_push,
        )
    except click.ClickException:
        raise
    except RuntimeError as e:
        emit_response(CommandResponse.error("team-secrets sync", "team", str(e)), **opts)
        sys.exit(1)

    # Check if result indicates conflicts in non-interactive mode
    if result.get("status") == "action-required":
        emit_response(
            CommandResponse(
                command="team-secrets sync",
                scope="team",
                status="action-required",
                summary=result.get("message", "Conflicts detected"),
                result=result,
                next_actions=["Resolve conflicts interactively or use --write-local-env"],
                warnings=[],
                errors=[],
            ),
            **opts,
        )
        sys.exit(2)

    # Push to GitHub if requested
    if not no_gh and not dry_run and not diff_only:
        try:
            import os
            from pathlib import Path as _Path

            from augint_tools.env.sync import perform_sync

            if os.environ.get("GH_REPO") or (_Path(".env").exists()):
                asyncio.run(perform_sync(".env", dry_run=dry_run))
        except Exception as e:
            result["gh_sync_error"] = str(e)

    prefix = "[DRY RUN] " if dry_run else ""
    summary = f"{prefix}Synced {project}/{env_name}"
    if diff_only:
        summary = f"Diff for {project}/{env_name}"

    emit_response(
        CommandResponse.ok(
            "team-secrets sync",
            "team",
            summary,
            result=result,
            next_actions=[f"ai-tools team-secrets {team} doctor"],
        ),
        **opts,
    )


# ---------------------------------------------------------------------------
# Admin subgroup: ai-tools team-secrets <team> admin ...
# ---------------------------------------------------------------------------


@team_secrets_group.group()
@click.pass_context
def admin(ctx):
    """Administrative commands (init, user management, rotation)."""
    ctx.ensure_object(dict)


@admin.command("init-repo")
@click.option(
    "--repo", "repo_path", default=None, help="Path to create the repo (default: ./<team>-secrets)."
)
@click.pass_context
def init_repo_cmd(ctx, repo_path):
    """Scaffold a new team secrets repository."""
    from pathlib import Path

    from augint_tools.team_secrets.repo import init_repo

    opts = _get_output_opts(ctx)
    team = ctx.obj["team"]

    if repo_path is None:
        repo_path = f"./{team}-secrets"

    path = Path(repo_path).expanduser().resolve()
    init_repo(path, team)

    emit_response(
        CommandResponse.ok(
            "team-secrets admin init-repo",
            "team",
            f"Scaffolded team repo at {path}",
            result={"path": str(path)},
            next_actions=[
                f"ai-tools team-secrets {team} admin add-user <name> --pubkey <key>",
                f"ai-tools team-secrets {team} admin init-project <project>",
            ],
        ),
        **opts,
    )


@admin.command("init-project")
@click.argument("project")
@click.pass_context
def init_project_cmd(ctx, project):
    """Initialize a new project within the team secrets repo."""
    from augint_tools.team_secrets.keys import load_team_config, resolve_repo_path
    from augint_tools.team_secrets.repo import init_project

    opts = _get_output_opts(ctx)
    team = ctx.obj["team"]

    config = load_team_config(team)
    repo_path = config.repo_path if config else resolve_repo_path(team)

    if repo_path is None:
        emit_response(
            CommandResponse.error(
                "team-secrets admin init-project",
                "team",
                f"Cannot locate team repo. Run: ai-tools team-secrets {team} setup",
            ),
            **opts,
        )
        sys.exit(1)

    init_project(repo_path, project, team)

    emit_response(
        CommandResponse.ok(
            "team-secrets admin init-project",
            "team",
            f"Initialized project '{project}'",
            result={"project": project, "path": str(repo_path / "projects" / project)},
            next_actions=[f"ai-tools team-secrets {team} edit {project} --env dev"],
        ),
        **opts,
    )


@admin.command("add-user")
@click.argument("name")
@click.option("--pubkey", default=None, help="Age public key (age1...).")
@click.option("--project", default=None, help="Add to project-specific recipients.")
@click.option(
    "--team-wide", is_flag=True, default=True, help="Add to team-wide recipients (default)."
)
@click.pass_context
def add_user_cmd(ctx, name, pubkey, project, team_wide):
    """Add a user to the team (generates encrypted key, updates recipients)."""

    from augint_tools.team_secrets.age import encrypt_with_password, generate_keypair
    from augint_tools.team_secrets.keys import load_team_config, resolve_repo_path
    from augint_tools.team_secrets.models import UserRecord
    from augint_tools.team_secrets.recipients import add_recipient, write_sops_yaml
    from augint_tools.team_secrets.sops import update_keys

    opts = _get_output_opts(ctx)
    team = ctx.obj["team"]

    config = load_team_config(team)
    repo_path = config.repo_path if config else resolve_repo_path(team)

    if repo_path is None:
        emit_response(
            CommandResponse.error(
                "team-secrets admin add-user",
                "team",
                f"Cannot locate team repo. Run: ai-tools team-secrets {team} setup",
            ),
            **opts,
        )
        sys.exit(1)

    # Generate keypair if no public key provided
    generated_keypair = None
    if pubkey is None:
        if sys.stdin.isatty():
            choice = click.prompt(
                "No --pubkey provided. [G]enerate a new keypair or [P]aste an existing public key?",
                type=click.Choice(["g", "p"], case_sensitive=False),
                default="g",
            )
            if choice.lower() == "p":
                pubkey = click.prompt("Paste age public key (age1...)")
            else:
                from augint_tools.team_secrets.age import generate_keypair

                generated_keypair = generate_keypair()
                pubkey = generated_keypair.public_key
                click.echo(f"Generated public key: {pubkey}")
        else:
            emit_response(
                CommandResponse.error(
                    "team-secrets admin add-user",
                    "team",
                    "--pubkey is required in non-interactive mode",
                ),
                **opts,
            )
            sys.exit(1)

    # Add to recipients
    user = UserRecord(name=name, public_key=pubkey)
    recipients_dir = repo_path / "recipients"

    if project:
        project_file = recipients_dir / f"project-{project}.txt"
        add_recipient(project_file, user)
        click.echo(f"Added to project recipients: {project}")

    # Always add to team-wide unless explicitly project-only
    team_file = recipients_dir / f"team-{team}.txt"
    add_recipient(team_file, user)
    click.echo(f"Added to team recipients: {team}")

    # Generate and store encrypted private key
    if generated_keypair:
        password = click.prompt(
            f"Set a password for {name}'s encrypted key",
            hide_input=True,
            confirmation_prompt=True,
        )
        encrypted = encrypt_with_password(generated_keypair.private_key, password)
        key_file = repo_path / "keys" / f"{name}.key.enc"
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_bytes(encrypted)
        click.echo(f"Encrypted private key saved to {key_file}")

    # Regenerate .sops.yaml
    write_sops_yaml(repo_path, team)
    click.echo("Regenerated .sops.yaml")

    # Update keys on existing encrypted files
    from augint_tools.team_secrets.keys import get_cached_key

    key_path = get_cached_key(team)
    if key_path:
        enc_files = list(repo_path.glob("projects/**/*.enc.env"))
        for f in enc_files:
            # Only update files that contain actual SOPS-encrypted content
            content = f.read_text()
            if "sops" in content or "ENC[" in content:
                try:
                    update_keys(f, key_path)
                except RuntimeError:
                    click.echo(f"  Warning: could not updatekeys on {f.name}")

    emit_response(
        CommandResponse.ok(
            "team-secrets admin add-user",
            "team",
            f"Added user '{name}' to team '{team}'",
            result={"name": name, "public_key": pubkey},
            next_actions=[
                f"Share password with {name} securely",
                f"ai-tools team-secrets {team} admin rotate --all",
            ],
        ),
        **opts,
    )


@admin.command("remove-user")
@click.argument("name")
@click.option("--project", default=None, help="Remove from specific project only.")
@click.option("--team-wide", is_flag=True, default=True, help="Remove from team-wide (default).")
@click.pass_context
def remove_user_cmd(ctx, name, project, team_wide):
    """Remove a user's access from the team."""
    from augint_tools.team_secrets.keys import get_cached_key, load_team_config, resolve_repo_path
    from augint_tools.team_secrets.recipients import remove_recipient, write_sops_yaml
    from augint_tools.team_secrets.sops import update_keys

    opts = _get_output_opts(ctx)
    team = ctx.obj["team"]

    config = load_team_config(team)
    repo_path = config.repo_path if config else resolve_repo_path(team)

    if repo_path is None:
        emit_response(
            CommandResponse.error(
                "team-secrets admin remove-user", "team", "Cannot locate team repo."
            ),
            **opts,
        )
        sys.exit(1)

    recipients_dir = repo_path / "recipients"
    removed = False

    if project:
        project_file = recipients_dir / f"project-{project}.txt"
        if remove_recipient(project_file, name):
            removed = True
            click.echo(f"Removed from project recipients: {project}")

    team_file = recipients_dir / f"team-{team}.txt"
    if remove_recipient(team_file, name):
        removed = True
        click.echo(f"Removed from team recipients: {team}")

    if not removed:
        emit_response(
            CommandResponse.error(
                "team-secrets admin remove-user", "team", f"User '{name}' not found in recipients"
            ),
            **opts,
        )
        sys.exit(1)

    # Delete encrypted key file
    key_file = repo_path / "keys" / f"{name}.key.enc"
    if key_file.exists():
        key_file.unlink()
        click.echo(f"Deleted {key_file}")

    # Regenerate .sops.yaml
    write_sops_yaml(repo_path, team)

    # Update keys on existing encrypted files
    key_path = get_cached_key(team)
    if key_path:
        enc_files = list(repo_path.glob("projects/**/*.enc.env"))
        for f in enc_files:
            content = f.read_text()
            if "sops" in content or "ENC[" in content:
                try:
                    update_keys(f, key_path)
                except RuntimeError:
                    click.echo(f"  Warning: could not updatekeys on {f.name}")

    click.echo(
        f"\nWarning: {name} still has any previously-decrypted secrets. "
        f"Consider rotating: ai-tools team-secrets {team} admin rotate --all"
    )

    emit_response(
        CommandResponse.ok(
            "team-secrets admin remove-user",
            "team",
            f"Removed user '{name}' from team '{team}'",
            result={"name": name},
            next_actions=[f"ai-tools team-secrets {team} admin rotate --all"],
        ),
        **opts,
    )


@admin.command("rotate")
@click.argument("project", required=False)
@click.option("--all", "rotate_all", is_flag=True, help="Rotate all projects.")
@click.pass_context
def rotate_cmd(ctx, project, rotate_all):
    """Rotate encryption keys (re-encrypt with current recipients)."""
    from augint_tools.team_secrets.keys import load_team_config, require_key
    from augint_tools.team_secrets.repo import list_projects
    from augint_tools.team_secrets.sops import update_keys

    opts = _get_output_opts(ctx)
    team = ctx.obj["team"]

    key_path = require_key(team)
    config = load_team_config(team)
    if not config:
        emit_response(
            CommandResponse.error("team-secrets admin rotate", "team", "No team config"), **opts
        )
        sys.exit(1)

    if not project and not rotate_all:
        if sys.stdin.isatty():
            projects = list_projects(config.repo_path)
            click.echo("Available projects:")
            for p in projects:
                click.echo(f"  - {p}")
            project = click.prompt("Project to rotate (or 'all')")
            if project == "all":
                rotate_all = True
        else:
            emit_response(
                CommandResponse.error(
                    "team-secrets admin rotate", "team", "Specify a project name or --all"
                ),
                **opts,
            )
            sys.exit(1)

    # Collect files to rotate
    if rotate_all:
        enc_files = list(config.repo_path.glob("projects/**/*.enc.env"))
    else:
        enc_files = list((config.repo_path / "projects" / project).glob("*.enc.env"))

    rotated: list[str] = []
    errors: list[str] = []

    for f in enc_files:
        content = f.read_text()
        if "sops" in content or "ENC[" in content:
            try:
                update_keys(f, key_path)
                rotated.append(str(f.relative_to(config.repo_path)))
            except RuntimeError as e:
                errors.append(f"{f.name}: {e}")

    emit_response(
        CommandResponse.ok(
            "team-secrets admin rotate",
            "team",
            f"Rotated {len(rotated)} files",
            result={"rotated": rotated, "errors": errors},
            warnings=errors,
        ),
        **opts,
    )


@admin.command("decrypt")
@click.argument("project")
@click.option("--env", "env_name", default="dev", help="Environment (default: dev).")
@click.option(
    "--stdout", "to_stdout", is_flag=True, default=True, help="Output to stdout (default)."
)
@click.option("--output", "output_path", default=None, help="Write to file instead of stdout.")
@click.pass_context
def decrypt_cmd(ctx, project, env_name, to_stdout, output_path):
    """Decrypt a project's env file (for debugging/export)."""
    from pathlib import Path

    from augint_tools.team_secrets.keys import load_team_config, require_key
    from augint_tools.team_secrets.repo import get_encrypted_env_path
    from augint_tools.team_secrets.sops import decrypt_file

    opts = _get_output_opts(ctx)
    team = ctx.obj["team"]

    key_path = require_key(team)
    config = load_team_config(team)
    if not config:
        emit_response(
            CommandResponse.error("team-secrets admin decrypt", "team", "No team config"), **opts
        )
        sys.exit(1)

    encrypted_path = get_encrypted_env_path(config.repo_path, project, env_name)
    if not encrypted_path.exists():
        emit_response(
            CommandResponse.error(
                "team-secrets admin decrypt", "team", f"No file at {encrypted_path}"
            ),
            **opts,
        )
        sys.exit(1)

    try:
        plaintext = decrypt_file(encrypted_path, key_path)
    except RuntimeError as e:
        emit_response(CommandResponse.error("team-secrets admin decrypt", "team", str(e)), **opts)
        sys.exit(1)

    if output_path:
        Path(output_path).write_text(plaintext)
        emit_response(
            CommandResponse.ok(
                "team-secrets admin decrypt",
                "team",
                f"Decrypted to {output_path}",
                result={"output": output_path},
            ),
            **opts,
        )
    else:
        # Write to stdout directly (not through emit_response)
        click.echo(plaintext, nl=False)


@admin.command("validate")
@click.argument("project")
@click.option("--env", "env_name", default="dev", help="Environment (default: dev).")
@click.pass_context
def validate_cmd(ctx, project, env_name):
    """Validate an encrypted env file (syntax, duplicates, schema)."""
    from augint_tools.team_secrets.keys import load_team_config, require_key
    from augint_tools.team_secrets.repo import get_encrypted_env_path
    from augint_tools.team_secrets.sops import decrypt_file
    from augint_tools.team_secrets.sync import parse_dotenv_content

    opts = _get_output_opts(ctx)
    team = ctx.obj["team"]

    key_path = require_key(team)
    config = load_team_config(team)
    if not config:
        emit_response(
            CommandResponse.error("team-secrets admin validate", "team", "No team config"), **opts
        )
        sys.exit(1)

    encrypted_path = get_encrypted_env_path(config.repo_path, project, env_name)
    if not encrypted_path.exists():
        emit_response(
            CommandResponse.error(
                "team-secrets admin validate", "team", f"No file at {encrypted_path}"
            ),
            **opts,
        )
        sys.exit(1)

    try:
        plaintext = decrypt_file(encrypted_path, key_path)
    except RuntimeError as e:
        emit_response(
            CommandResponse.error("team-secrets admin validate", "team", f"Decrypt failed: {e}"),
            **opts,
        )
        sys.exit(1)

    # Parse and validate
    issues: list[str] = []
    data = parse_dotenv_content(plaintext)

    # Check for malformed lines
    for i, line in enumerate(plaintext.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("export "):
            continue
        if "=" not in stripped:
            issues.append(f"Line {i}: malformed (no '=' found): {stripped[:40]}")

    # Check for duplicate keys (parse_dotenv_content deduplicates, so check raw)
    seen_keys: dict[str, int] = {}
    for i, line in enumerate(plaintext.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:]
        if "=" in stripped:
            key = stripped.partition("=")[0].strip()
            if key in seen_keys:
                issues.append(f"Line {i}: duplicate key '{key}' (first at line {seen_keys[key]})")
            else:
                seen_keys[key] = i

    # Check for empty values
    for key, value in data.items():
        if not value:
            issues.append(f"Empty value for key '{key}'")

    # Classify entries
    try:
        # Write temp file for classify_env
        import tempfile

        from augint_tools.env.classify import Classification, classify_env

        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as tmp:
            tmp.write(plaintext)
            tmp_path = tmp.name
        results = classify_env(tmp_path)
        import os

        os.unlink(tmp_path)

        secrets = [r for r in results if r.classification == Classification.SECRET]
        variables = [r for r in results if r.classification == Classification.VARIABLE]
        classification = {
            "secrets": [r.key for r in secrets],
            "variables": [r.key for r in variables],
        }
    except Exception:
        classification = {}

    # Check schema if exists
    schema_path = config.repo_path / "projects" / project / "schema.yaml"
    schema_issues: list[str] = []
    if schema_path.exists():
        import yaml

        with open(schema_path) as f:
            schema = yaml.safe_load(f) or {}
        for entry in schema.get("keys", []):
            key_name = entry.get("name", "")
            if entry.get("required") and key_name not in data:
                schema_issues.append(f"Required key '{key_name}' missing")

    all_issues = issues + schema_issues
    status = "ok" if not all_issues else "action-required"
    summary = f"{len(data)} keys, {len(all_issues)} issues"

    emit_response(
        CommandResponse(
            command="team-secrets admin validate",
            scope="team",
            status=status,
            summary=summary,
            result={
                "keys_count": len(data),
                "issues": all_issues,
                "classification": classification,
            },
            next_actions=[f"Fix: {i}" for i in all_issues[:3]] if all_issues else [],
            warnings=[],
            errors=all_issues,
        ),
        **opts,
    )
    if all_issues:
        sys.exit(2)
