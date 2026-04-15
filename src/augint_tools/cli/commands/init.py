"""Top-level interactive wizard.

Walks the user through every file/setting `ai-tools` can manage. Each step is
isolated: failures log the reason and continue to the next step rather than
aborting the whole wizard.

Adding a new step: append an :class:`InitStep` to ``INIT_STEPS``.  The wizard
will pick it up automatically, ask the user, and handle errors uniformly.
"""

from __future__ import annotations

import os
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
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
)
from augint_tools.ide.detect import (
    bootstrap_github_env,
    detect_project_name,
    detect_python_version,
    ensure_iml_file,
    ensure_project_root_manager,
    external_storage_enabled,
    find_iml_file,
    find_jb_options_dir,
    parse_dotenv,
    parse_git_remote,
    resolve_product_workspace,
    resolve_windows_paths,
    upsert_dotenv,
)

# ---------------------------------------------------------------------------
# Context: everything the steps might need, detected up front.
# ---------------------------------------------------------------------------


@dataclass
class InitContext:
    """Detected environment passed to every step."""

    project_dir: str
    project_name: str
    venv_path: str
    sdk_name: str
    full_ver: str
    major_minor: str
    iml_path: str | None
    workspace_path: str
    misc_path: str
    has_idea: bool
    has_git: bool
    has_venv: bool
    has_pyproject: bool
    git_remote: tuple[str, str, str] | None
    win_proj: str | None
    win_venv: str | None
    win_python: str | None
    jb_options: str | None
    product_ws: str | None
    gh_token: str
    external_project_storage: bool
    warnings: list[str] = field(default_factory=list)


def _build_context(project_dir: str, verbose: bool = False) -> InitContext:
    """Detect everything the steps might need. Always succeeds."""
    pdir = os.path.realpath(project_dir)
    venv_path = os.path.join(pdir, ".venv")
    workspace_path = os.path.join(pdir, ".idea", "workspace.xml")
    misc_path = os.path.join(pdir, ".idea", "misc.xml")
    external_project_storage = external_storage_enabled(misc_path)

    full_ver, major_minor = detect_python_version(venv_path)
    project_name = detect_project_name(pdir)
    sdk_name = f"Python {major_minor} ({project_name})"
    iml_path = find_iml_file(pdir)

    # Normalize module storage into .idea/ when this project is using local
    # project files instead of IntelliJ's external generated-file storage.
    if os.path.isdir(os.path.join(pdir, ".idea")) and not external_project_storage:
        iml_path = ensure_iml_file(pdir, project_name)
    if os.path.isdir(os.path.join(pdir, ".idea")):
        ensure_project_root_manager(misc_path)

    win_proj, win_venv, win_python = resolve_windows_paths(pdir, venv_path, workspace_path, None)
    jb_options = find_jb_options_dir()
    product_ws = resolve_product_workspace(jb_options, workspace_path)

    env = parse_dotenv(os.path.join(pdir, ".env"))
    gh_token = env.get("GH_TOKEN") or os.environ.get("GH_TOKEN", "")

    git_remote = parse_git_remote(pdir)

    warnings: list[str] = []
    if not os.path.exists(venv_path):
        warnings.append("No .venv -- IDE SDK steps will be skipped")
    if not os.path.isdir(os.path.join(pdir, ".idea")):
        warnings.append("No .idea/ -- IDE steps will be skipped (open project in IDEA first)")
    if not os.path.isdir(os.path.join(pdir, ".git")):
        warnings.append("No .git/ -- GitHub task server step will be skipped")

    return InitContext(
        project_dir=pdir,
        project_name=project_name,
        venv_path=venv_path,
        sdk_name=sdk_name,
        full_ver=full_ver,
        major_minor=major_minor,
        iml_path=iml_path,
        workspace_path=workspace_path,
        misc_path=misc_path,
        has_idea=os.path.isdir(os.path.join(pdir, ".idea")),
        has_git=os.path.isdir(os.path.join(pdir, ".git")),
        has_venv=os.path.exists(venv_path),
        has_pyproject=os.path.exists(os.path.join(pdir, "pyproject.toml")),
        git_remote=git_remote,
        win_proj=win_proj,
        win_venv=win_venv,
        win_python=win_python,
        jb_options=jb_options,
        product_ws=product_ws,
        gh_token=gh_token,
        external_project_storage=external_project_storage,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Step definitions
# ---------------------------------------------------------------------------


@dataclass
class InitStep:
    """One step in the init wizard.

    To add a new step, define another instance and append it to ``INIT_STEPS``.
    """

    id: str
    label: str
    description: str
    applicable: Callable[[InitContext], tuple[bool, str]]  # (yes/no, reason if no)
    run: Callable[[InitContext], StepResult]
    default_yes: bool = True


def _ok(name: str, msg: str, **details: Any) -> StepResult:
    return StepResult(name=name, status="ok", message=msg, details=dict(details))


def _skip(name: str, msg: str) -> StepResult:
    return StepResult(name=name, status="skipped", message=msg)


def _err(name: str, msg: str) -> StepResult:
    return StepResult(name=name, status="error", message=msg)


# --- Step actions ---


def _run_dotenv_bootstrap(c: InitContext) -> StepResult:
    """Ensure .env exists with GH_ACCOUNT and GH_REPO pre-populated."""
    env_path = os.path.join(c.project_dir, ".env")
    owner = c.git_remote[0] if c.git_remote else ""
    repo = c.git_remote[1] if c.git_remote else ""
    written = bootstrap_github_env(env_path, owner=owner, repo=repo)
    display_written = [entry if not entry.endswith("=") else f"{entry}(blank)" for entry in written]

    if not written:
        return _skip("dotenv", "GH_ACCOUNT and GH_REPO already present in .env")
    return _ok("dotenv", f"Wrote to {env_path}: {', '.join(display_written)}")


def _run_gh_token(c: InitContext) -> StepResult:
    """Prompt for GH_TOKEN and save to .env."""
    from datetime import UTC, datetime

    now = datetime.now(tz=UTC).strftime("%d/%m/%Y %H:%M:%S")
    token_name = f"{c.project_name} init {now}"

    click.echo("  Create a fine-grained personal access token:")
    click.echo("  https://github.com/settings/personal-access-tokens/new")
    click.echo("")
    click.echo(f"  Token name : {token_name}")
    click.echo("  Expiration : 1 year (maximum)")
    if c.git_remote:
        click.echo(f"  Repository : {c.git_remote[0]}/{c.git_remote[1]} (select this repo)")
    else:
        click.echo("  Repository : (select the target repo)")
    click.echo("")
    click.echo("  Recommended repo permissions:")
    click.echo("    Actions              : Read and write")
    click.echo("    Administration       : Read and write")
    click.echo("    Discussions          : Read and write")
    click.echo("    Metadata             : Read-only (always on)")
    click.echo("    Pages                : Read and write")
    click.echo("    Secrets              : Read and write")
    click.echo("    Variables            : Read and write")
    click.echo("    Workflows            : Read and write")
    click.echo("")
    click.echo("  Often useful for repo automation:")
    click.echo("    Contents             : Read and write")
    click.echo("    Pull requests        : Read and write")
    click.echo("")
    click.echo("  Optional account permissions:")
    click.echo("    Gists                : Write")
    click.echo("")
    raw: str = click.prompt(
        "  Paste GitHub token (blank to skip)",
        hide_input=True,
        default="",
        show_default=False,
    )
    token = raw.strip()
    if not token:
        return _skip("gh_token", "No token entered")
    env_path = os.path.join(c.project_dir, ".env")
    bootstrap_github_env(
        env_path,
        owner=c.git_remote[0] if c.git_remote else "",
        repo=c.git_remote[1] if c.git_remote else "",
    )
    upsert_dotenv(env_path, "GH_TOKEN", token)
    c.gh_token = token  # update context for downstream steps
    os.environ["GH_TOKEN"] = token
    return _ok("gh_token", f"Saved GH_TOKEN to {env_path}")


def _run_module_sdk(c: InitContext) -> StepResult:
    return step_module_sdk(c.iml_path, c.sdk_name)


def _run_structure(c: InitContext) -> StepResult:
    return step_project_structure(c.iml_path, c.project_dir, c.project_name)


def _run_project_sdk(c: InitContext) -> StepResult:
    return step_project_sdk(c.misc_path, c.sdk_name)


def _run_github_tasks(c: InitContext) -> StepResult:
    return step_github_tasks(c.workspace_path, c.project_dir, c.gh_token, c.product_ws)


def _run_jdk_table(c: InitContext) -> StepResult:
    return step_jdk_table(c.jb_options, c.sdk_name, c.full_ver, c.win_python, c.win_venv)


def _run_bookmarks(c: InitContext) -> StepResult:
    return step_bookmarks(c.project_dir, c.project_name, c.workspace_path, c.product_ws)


def _run_reset_prompt(c: InitContext) -> StepResult:
    """Offer to delete the product workspace file so changes take effect."""
    if c.product_ws is None or not os.path.exists(c.product_ws):
        return _skip("reset", "No product workspace file to reset")
    click.echo(f"  Will delete: {c.product_ws}")
    click.echo(
        click.style(
            "  IMPORTANT: close this project's IDEA window first (File -> Close Project).",
            fg="yellow",
        )
    )
    if not click.confirm("  Project window closed -- proceed?", default=False):
        return _skip("reset", "User declined to close project window")
    try:
        os.remove(c.product_ws)
    except OSError as e:
        return _err("reset", f"Failed to delete: {e}")
    return _ok("reset", f"Deleted {c.product_ws}")


# --- The ordered step list ---


INIT_STEPS: list[InitStep] = [
    InitStep(
        id="dotenv",
        label="Bootstrap .env (GH_ACCOUNT, GH_REPO)",
        description=(
            "Create .env if missing and populate GH_ACCOUNT and GH_REPO "
            "from the git remote origin. Values are left blank when not detectable."
        ),
        applicable=lambda c: (True, ""),
        run=_run_dotenv_bootstrap,
    ),
    InitStep(
        id="gh_token",
        label="GitHub token (.env)",
        description=(
            "Save a GitHub personal access token to .env as GH_TOKEN. "
            "Used by IDE Tasks server, gh CLI, and semantic-release."
        ),
        applicable=lambda c: (
            (False, f"GH_TOKEN already set ({c.gh_token[:6]}...)") if c.gh_token else (True, "")
        ),
        run=_run_gh_token,
        default_yes=True,
    ),
    InitStep(
        id="module_sdk",
        label="IDE: module SDK",
        description="Set the Python SDK reference in the .iml module file.",
        applicable=lambda c: (
            (False, "generated module files are stored externally by IntelliJ")
            if c.external_project_storage and c.iml_path is None
            else (False, "no .iml file found")
            if c.iml_path is None
            else (True, "")
        ),
        run=_run_module_sdk,
    ),
    InitStep(
        id="structure",
        label="IDE: project source/test/exclude roots",
        description="Mark src/, tests/ as source roots; mark dist/, .venv/, caches as excluded.",
        applicable=lambda c: (
            (False, "generated module files are stored externally by IntelliJ")
            if c.external_project_storage and c.iml_path is None
            else (False, "no .iml file found")
            if c.iml_path is None
            else (True, "")
        ),
        run=_run_structure,
    ),
    InitStep(
        id="project_sdk",
        label="IDE: project SDK (misc.xml)",
        description="Set the project-level Python SDK in .idea/misc.xml.",
        applicable=lambda c: (
            (False, "no .idea/misc.xml found") if not os.path.exists(c.misc_path) else (True, "")
        ),
        run=_run_project_sdk,
    ),
    InitStep(
        id="github_tasks",
        label="IDE: GitHub Tasks server",
        description="Add a <GitHub> server entry to .idea/workspace.xml. Seeds GH_TOKEN once.",
        applicable=lambda c: (
            (False, "no GitHub remote in .git/config") if c.git_remote is None else (True, "")
        ),
        run=_run_github_tasks,
    ),
    InitStep(
        id="jdk_table",
        label="IDE: register SDK in JetBrains config",
        description=(
            "Add the venv as a Python SDK entry in jdk.table.xml so IDEA can find it. "
            "Skips silently if JB config dir is not found (e.g. running from non-Windows shell)."
        ),
        applicable=lambda c: (
            (False, "JB config dir not found")
            if c.jb_options is None
            else (False, "Windows project path not resolved")
            if c.win_python is None
            else (True, "")
        ),
        run=_run_jdk_table,
    ),
    InitStep(
        id="bookmarks",
        label="IDE: mnemonic bookmarks",
        description=(
            "Auto-detect key files (pyproject.toml, README.md, CLAUDE.md, entry point, "
            "CI workflow, .env, AGENTS.md, pre-commit) and bookmark them with mnemonics 1-9."
        ),
        applicable=lambda c: (
            (False, "no .idea/workspace.xml")
            if not os.path.exists(c.workspace_path)
            else (True, "")
        ),
        run=_run_bookmarks,
    ),
    InitStep(
        id="reset",
        label="IDE: force IDEA to reload workspace.xml",
        description=(
            "Delete the per-project product workspace cache so IDEA re-reads "
            ".idea/workspace.xml on next open. Required after IDE-related changes "
            "if IDEA already had cached state."
        ),
        applicable=lambda c: (
            (False, "no product workspace file to reset") if c.product_ws is None else (True, "")
        ),
        run=_run_reset_prompt,
        default_yes=False,  # destructive-ish; default to no
    ),
]


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _icon(status: str) -> str:
    if status == "ok":
        return click.style("[ok]", fg="green")
    if status == "skipped":
        return click.style("[skip]", fg="blue")
    if status == "action-required":
        return click.style("[action]", fg="yellow")
    if status == "error":
        return click.style("[error]", fg="red")
    return click.style(f"[{status}]", fg="yellow")


def _print_header(c: InitContext) -> None:
    click.echo(click.style("ai-tools init -- interactive wizard", bold=True))
    click.echo("")
    click.echo("Detected environment:")
    click.echo(f"  Project       : {c.project_dir}")
    click.echo(f"  Name          : {c.project_name}")
    click.echo(
        f"  Python (venv) : {c.full_ver if c.has_venv else click.style('(none)', fg='yellow')}"
    )
    click.echo(f"  IDEA project  : {'yes' if c.has_idea else click.style('no', fg='yellow')}")
    click.echo(f"  Git repo      : {'yes' if c.has_git else click.style('no', fg='yellow')}")
    if c.git_remote:
        click.echo(f"  GitHub remote : {c.git_remote[2]}")
    click.echo(f"  GH_TOKEN      : {'set' if c.gh_token else click.style('not set', fg='yellow')}")
    if c.jb_options:
        click.echo(f"  JB config     : {c.jb_options}")
    if c.product_ws:
        click.echo(f"  Product WS    : {c.product_ws}")
    if c.warnings:
        click.echo("")
        for w in c.warnings:
            click.echo(click.style(f"  Warning: {w}", fg="yellow"))
    click.echo("")


def _print_summary(results: list[tuple[str, StepResult]]) -> None:
    click.echo("")
    click.echo(click.style("=== Summary ===", bold=True))
    counts: dict[str, int] = {"ok": 0, "skipped": 0, "action-required": 0, "error": 0}
    for label, r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
        click.echo(f"  {_icon(r.status)} {label}: {r.message}")
    click.echo("")
    parts = [f"{n} {s}" for s, n in counts.items() if n > 0]
    click.echo(click.style(f"Done. {', '.join(parts)}.", bold=True))


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


@click.command("init")
@click.option(
    "--project-dir",
    default=".",
    show_default=True,
    type=click.Path(exists=True),
    help="Project root to operate on.",
)
@click.option(
    "-y",
    "--yes",
    is_flag=True,
    default=False,
    help="Run all applicable steps without prompting (still skips inapplicable ones).",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    default=False,
    help="Print full tracebacks on step failures.",
)
@click.pass_context
def init(ctx: click.Context, project_dir: str, yes: bool, verbose: bool) -> None:
    """Interactive setup wizard.

    Walks through every file `ai-tools` can create or modify, asking before
    each one. Failures in a single step do not stop the wizard -- the step is
    marked as errored and the next step proceeds. Run again any time; steps
    detect existing state and skip themselves when nothing needs changing.
    """
    c = _build_context(project_dir, verbose=verbose)
    _run_dotenv_bootstrap(c)
    _print_header(c)

    # IDEA-running warning
    if c.has_idea:
        click.echo(
            click.style(
                "Note: if IDEA is open with this project, close that project window first "
                "(File -> Close Project) for IDE changes to stick.",
                fg="yellow",
            )
        )
        click.echo("")

    results: list[tuple[str, StepResult]] = []
    applicable_steps: list[InitStep] = []
    for step in INIT_STEPS:
        ok, reason = step.applicable(c)
        if not ok:
            click.echo(f"{_icon('skipped')} {step.label}: {reason}")
            results.append((step.label, _skip(step.id, reason)))
            continue
        applicable_steps.append(step)

    if not applicable_steps:
        click.echo("")
        click.echo(click.style("No applicable steps detected. Nothing to do.", bold=True))
        return

    click.echo("")
    click.echo(click.style(f"{len(applicable_steps)} step(s) applicable:", bold=True))
    for s in applicable_steps:
        click.echo(f"  - {s.label}")
    click.echo("")

    for i, step in enumerate(applicable_steps, start=1):
        click.echo(click.style(f"[{i}/{len(applicable_steps)}] {step.label}", bold=True))
        click.echo(f"  {step.description}")

        if not yes and not click.confirm("  Run this step?", default=step.default_yes):
            results.append((step.label, _skip(step.id, "User declined")))
            click.echo(f"  {_icon('skipped')} skipped by user")
            click.echo("")
            continue

        try:
            result = step.run(c)
        except Exception as exc:  # noqa: BLE001 -- intentional broad except for resilience
            msg = f"{type(exc).__name__}: {exc}"
            results.append((step.label, _err(step.id, msg)))
            click.echo(f"  {_icon('error')} {msg}")
            if verbose:
                click.echo(click.style(traceback.format_exc(), fg="bright_black"))
            click.echo("")
            continue

        results.append((step.label, result))
        click.echo(f"  {_icon(result.status)} {result.message}")
        if result.next_action and result.status == "action-required":
            click.echo(f"    -> {result.next_action}")
        # Show bookmark table inline
        if step.id == "bookmarks" and result.details.get("table"):
            for line in result.details["table"]:
                click.echo(line)
        click.echo("")

    _print_summary(results)
