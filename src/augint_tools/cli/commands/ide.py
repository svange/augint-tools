"""IntelliJ IDEA project setup commands."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from typing import Any

import click

from augint_tools.ide import (
    StepResult,
    step_bookmarks,
    step_github_tasks,
    step_jdk_table,
    step_module_sdk,
    step_project_sdk,
    step_project_structure,
    step_terminal_right,
)
from augint_tools.ide.detect import (
    detect_project_name,
    detect_python_version,
    find_iml_file,
    find_jb_options_dir,
    parse_dotenv,
    resolve_product_workspace,
    resolve_windows_paths,
    upsert_dotenv,
)
from augint_tools.output import CommandResponse, emit_response

ALL_STEPS = [
    "terminal",
    "module_sdk",
    "structure",
    "project_sdk",
    "github_tasks",
    "jdk_table",
    "bookmarks",
]


def _get_output_opts(ctx: click.Context) -> dict[str, Any]:
    obj = ctx.obj or {}
    return {
        "json_mode": obj.get("json_mode", False),
        "actionable": obj.get("actionable", False),
        "summary_only": obj.get("summary_only", False),
    }


def _is_json(ctx: click.Context) -> bool:
    return bool((ctx.obj or {}).get("json_mode", False))


@click.group()
@click.pass_context
def ide(ctx: click.Context) -> None:
    """IntelliJ IDEA project setup (SDK, tasks, layout)."""
    ctx.ensure_object(dict)


# ---------------------------------------------------------------------------
# ide info
# ---------------------------------------------------------------------------


@ide.command("info")
@click.option("--project-dir", default=".", show_default=True, type=click.Path(exists=True))
@click.option("--venv-path", default=None, help="Path to venv (default: <project-dir>/.venv).")
@click.pass_context
def info(ctx: click.Context, project_dir: str, venv_path: str | None) -> None:
    """Print detected IDE/project state without making any changes."""
    pdir = os.path.realpath(project_dir)
    vpath = venv_path or os.path.join(pdir, ".venv")

    full_ver, major_minor = detect_python_version(vpath)
    project_name = detect_project_name(pdir)
    sdk_name = f"Python {major_minor} ({project_name})"

    idea_dir = os.path.join(pdir, ".idea")
    workspace_path = os.path.join(idea_dir, "workspace.xml")
    iml_path = find_iml_file(pdir)

    win_proj, win_venv, win_python = resolve_windows_paths(pdir, vpath, workspace_path, None)
    jb_options = find_jb_options_dir()

    env = parse_dotenv(os.path.join(pdir, ".env"))
    gh_token_present = bool(env.get("GH_TOKEN") or os.environ.get("GH_TOKEN"))

    emit_response(
        CommandResponse.ok(
            "ide info",
            "ide",
            f"{project_name} | {sdk_name}",
            result={
                "project_dir": pdir,
                "project_name": project_name,
                "venv_path": vpath,
                "python_version": full_ver,
                "sdk_name": sdk_name,
                "iml_path": iml_path,
                "idea_dir_exists": os.path.isdir(idea_dir),
                "workspace_xml_exists": os.path.exists(workspace_path),
                "windows_project_dir": win_proj,
                "windows_venv": win_venv,
                "windows_python": win_python,
                "jb_options_dir": jb_options,
                "gh_token_present": gh_token_present,
            },
        ),
        **_get_output_opts(ctx),
    )


# ---------------------------------------------------------------------------
# ide setup
# ---------------------------------------------------------------------------


@ide.command("setup")
@click.option(
    "-i",
    "--interactive",
    is_flag=True,
    default=False,
    help="Prompt for missing inputs and retry blocked steps.",
)
@click.option("--project-dir", default=".", show_default=True, type=click.Path(exists=True))
@click.option("--venv-path", default=None, help="Path to venv (default: <project-dir>/.venv).")
@click.option("--sdk-name", default=None, help="Override auto-derived SDK name.")
@click.option(
    "--windows-project-dir",
    default=None,
    help="Windows project path (e.g. C:/Users/you/projects/foo).",
)
@click.option(
    "--skip",
    default=None,
    help=f"Comma-separated steps to skip ({','.join(ALL_STEPS)}).",
)
@click.option("--dry-run", is_flag=True, default=False, help="Show planned edits without writing.")
@click.option(
    "-v", "--verbose", is_flag=True, default=False, help="Show debug details for each step."
)
@click.pass_context
def setup(
    ctx: click.Context,
    interactive: bool,
    project_dir: str,
    venv_path: str | None,
    sdk_name: str | None,
    windows_project_dir: str | None,
    skip: str | None,
    dry_run: bool,
    verbose: bool,
) -> None:
    """Configure IntelliJ IDEA for a Python project.

    Applies up to seven steps: terminal panel, module SDK, project structure,
    project SDK, GitHub Tasks server, global SDK registration, and bookmarks.
    """
    opts = _get_output_opts(ctx)
    pdir = os.path.realpath(project_dir)
    vpath = venv_path or os.path.join(pdir, ".venv")
    full_ver, major_minor = detect_python_version(vpath)
    project_name = detect_project_name(pdir)
    effective_sdk_name = sdk_name or f"Python {major_minor} ({project_name})"

    idea_dir = os.path.join(pdir, ".idea")
    workspace_path = os.path.join(idea_dir, "workspace.xml")
    misc_path = os.path.join(idea_dir, "misc.xml")
    iml_path = find_iml_file(pdir)

    skip_set = {s.strip() for s in (skip.split(",") if skip else []) if s.strip()}
    unknown_skips = skip_set - set(ALL_STEPS)
    if unknown_skips:
        emit_response(
            CommandResponse.error(
                "ide setup",
                "ide",
                f"Unknown --skip values: {', '.join(sorted(unknown_skips))}. "
                f"Valid: {', '.join(ALL_STEPS)}",
            ),
            **opts,
        )
        sys.exit(1)

    json_mode = _is_json(ctx)
    human = not json_mode

    warnings: list[str] = []
    if _idea_running():
        msg = "IntelliJ IDEA appears to be running; changes may be overwritten on exit"
        if interactive:
            click.echo(click.style(f"Warning: {msg}", fg="yellow"))
            if not click.confirm("Continue anyway?", default=False):
                emit_response(
                    CommandResponse(
                        command="ide setup",
                        scope="ide",
                        status="blocked",
                        summary="Aborted: IntelliJ IDEA must be closed first",
                        warnings=[msg],
                        next_actions=["Close IntelliJ IDEA, then re-run `ai-tools ide setup`"],
                    ),
                    **opts,
                )
                sys.exit(3)
        else:
            warnings.append(msg)

    env = parse_dotenv(os.path.join(pdir, ".env"))
    win_proj, win_venv, win_python = resolve_windows_paths(
        pdir, vpath, workspace_path, windows_project_dir
    )
    jb_options = find_jb_options_dir()
    product_ws = resolve_product_workspace(jb_options, workspace_path)

    if verbose and human:
        from augint_tools.ide.detect import extract_project_id

        pid = extract_project_id(workspace_path)
        click.echo(click.style("  [debug] Detection details:", fg="bright_black"))
        click.echo(
            click.style(
                f"    workspace.xml  : {workspace_path} (exists={os.path.exists(workspace_path)})",
                fg="bright_black",
            )
        )
        click.echo(click.style(f"    ProjectId      : {pid or '(none)'}", fg="bright_black"))
        if jb_options:
            config_root = os.path.dirname(jb_options)
            ws_dir = os.path.join(config_root, "workspace")
            click.echo(
                click.style(
                    f"    workspace dir  : {ws_dir} (exists={os.path.isdir(ws_dir)})",
                    fg="bright_black",
                )
            )
            if pid:
                candidate = os.path.join(ws_dir, f"{pid}.xml")
                click.echo(
                    click.style(
                        f"    candidate file : {candidate} (exists={os.path.exists(candidate)})",
                        fg="bright_black",
                    )
                )
        click.echo(click.style(f"    product_ws     : {product_ws or '(none)'}", fg="bright_black"))
        click.echo(
            click.style(
                f"    GH_TOKEN       : {'set' if env.get('GH_TOKEN') or os.environ.get('GH_TOKEN') else 'not set'}",
                fg="bright_black",
            )
        )
        click.echo("")

    # Always show header in human mode (not just interactive)
    if human:
        click.echo(click.style("IntelliJ IDEA Project Setup", bold=True))
        click.echo(f"  Project  : {pdir}")
        click.echo(f"  SDK name : {effective_sdk_name}")
        click.echo(f"  Python   : {full_ver}")
        if iml_path:
            click.echo(f"  IML file : {iml_path}")
        else:
            click.echo(click.style("  IML file : (none found)", fg="yellow"))
        if win_proj:
            click.echo(f"  Win path : {win_proj}")
        if jb_options:
            click.echo(f"  JB config: {jb_options}")
        if product_ws:
            click.echo(f"  Prod WS  : {product_ws}")
        else:
            click.echo(
                click.style("  Prod WS  : (not found — open project in IDEA first)", fg="yellow")
            )
        if dry_run:
            click.echo(click.style("  Mode     : dry-run (no files will be written)", fg="cyan"))
        click.echo("")

    results: list[StepResult] = []

    def _dbg(msg: str) -> None:
        if verbose and human:
            click.echo(click.style(f"    [debug] {msg}", fg="bright_black"))

    def _run(step_name: str, fn: Callable[[], StepResult]) -> StepResult:
        if step_name in skip_set:
            res = StepResult(name=step_name, status="skipped", message="skipped via --skip")
            _echo(res, human)
            return res
        res = fn()
        _echo(res, human)
        if verbose and human and res.details:
            for k, v in res.details.items():
                if k == "table":
                    continue  # shown separately
                _dbg(f"{k}={v}")
        return res

    results.append(
        _run("terminal", lambda: step_terminal_right(workspace_path, product_ws, dry_run))
    )
    results.append(
        _run("module_sdk", lambda: step_module_sdk(iml_path, effective_sdk_name, dry_run))
    )
    results.append(
        _run(
            "structure",
            lambda: step_project_structure(iml_path, pdir, project_name, dry_run),
        )
    )
    results.append(
        _run("project_sdk", lambda: step_project_sdk(misc_path, effective_sdk_name, dry_run))
    )

    # github_tasks — token not stored in XML; IDEA uses OS keyring
    gh_token = env.get("GH_TOKEN") or os.environ.get("GH_TOKEN", "")
    results.append(
        _run(
            "github_tasks",
            lambda: step_github_tasks(workspace_path, pdir, gh_token, product_ws, dry_run),
        )
    )

    # jdk_table — interactive: prompt for windows path on action-required and retry once
    if "jdk_table" in skip_set:
        jdk_res = StepResult(name="jdk_table", status="skipped", message="skipped via --skip")
        _echo(jdk_res, human)
    else:
        jdk_res = step_jdk_table(
            jb_options, effective_sdk_name, full_ver, win_python, win_venv, dry_run
        )
        _echo(jdk_res, human)
        if (
            interactive
            and jdk_res.status == "action-required"
            and "windows_project_dir" in jdk_res.missing_inputs
        ):
            raw_win: str = click.prompt(
                "  Windows project path (e.g. C:/Users/you/projects/foo)",
                type=str,
            )
            new_win = raw_win.strip()
            if new_win:
                win_proj, win_venv, win_python = resolve_windows_paths(
                    pdir, vpath, workspace_path, new_win
                )
                jdk_res = step_jdk_table(
                    jb_options, effective_sdk_name, full_ver, win_python, win_venv, dry_run
                )
                _echo(jdk_res, human, retried=True)
    results.append(jdk_res)

    # bookmarks — detect files and write to product workspace
    bm_res = _run(
        "bookmarks",
        lambda: step_bookmarks(pdir, project_name, product_ws, dry_run),
    )
    # Show bookmark table in human mode when files were found
    if human and bm_res.details.get("table"):
        for line in bm_res.details["table"]:
            click.echo(line)
    results.append(bm_res)

    if human:
        click.echo("")  # blank line before final summary

    response = _aggregate(results, warnings, pdir, effective_sdk_name, full_ver, dry_run)
    emit_response(response, **opts)
    sys.exit(response.exit_code)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _idea_running() -> bool:
    """Best-effort detection of a running IntelliJ IDEA process."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "idea"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _echo(result: StepResult, human: bool, retried: bool = False) -> None:
    """Print a per-step progress line in human (non-JSON) mode."""
    if not human:
        return
    prefix = "    " if retried else "  "
    if result.status == "ok":
        icon = click.style("[ok]", fg="green")
    elif result.status == "skipped":
        icon = click.style("[skip]", fg="blue")
    elif result.status == "action-required":
        icon = click.style("[action]", fg="yellow")
    else:
        icon = click.style("[error]", fg="red")
    click.echo(f"{prefix}{icon} {result.name}: {result.message}")
    if result.next_action and result.status == "action-required":
        click.echo(f"{prefix}  -> {result.next_action}")


def _prompt_gh_token(project_dir: str) -> str | None:
    """Prompt for a GitHub token and optionally save it to .env."""
    click.echo(
        "  Create a token at https://github.com/settings/tokens/new"
        "?scopes=repo,read:user&description=IntelliJ+Tasks"
    )
    raw: str = click.prompt(
        "  Paste GitHub token (blank to skip)",
        hide_input=True,
        default="",
        show_default=False,
    )
    token = raw.strip()
    if not token:
        return None
    if click.confirm("  Save GH_TOKEN to .env?", default=True):
        upsert_dotenv(os.path.join(project_dir, ".env"), "GH_TOKEN", token)
        click.echo(click.style("  [ok] saved GH_TOKEN to .env", fg="green"))
    return token


def _aggregate(
    results: list[StepResult],
    warnings: list[str],
    project_dir: str,
    sdk_name: str,
    full_ver: str,
    dry_run: bool,
) -> CommandResponse:
    ok_count = sum(1 for r in results if r.status in ("ok", "skipped"))
    action_count = sum(1 for r in results if r.status == "action-required")
    error_count = sum(1 for r in results if r.status == "error")

    if error_count and ok_count == 0 and action_count == 0:
        status = "error"
    elif error_count:
        status = "partial"
    elif action_count:
        status = "action-required"
    else:
        status = "ok"

    parts = []
    if ok_count:
        parts.append(f"{ok_count} ok/skipped")
    if action_count:
        parts.append(f"{action_count} blocked")
    if error_count:
        parts.append(f"{error_count} errored")
    summary = ", ".join(parts) or "no steps run"
    if dry_run:
        summary = f"[dry-run] {summary}"

    next_actions = [r.next_action for r in results if r.next_action]
    errors = [f"{r.name}: {r.message}" for r in results if r.status == "error"]

    return CommandResponse(
        command="ide setup",
        scope="ide",
        status=status,
        summary=summary,
        warnings=warnings,
        errors=errors,
        next_actions=next_actions,
        result={
            "project_dir": project_dir,
            "sdk_name": sdk_name,
            "python_version": full_ver,
            "dry_run": dry_run,
            "steps": [r.to_dict() for r in results],
        },
    )
