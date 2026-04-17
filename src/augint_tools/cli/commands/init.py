"""New project scaffolding wizard (ai-tools init).

Guides the user through selecting a project type and setting up scaffolding
using the appropriate tools (uv, npm, cdk, sam, npx).  After scaffolding,
prints next-step instructions including running ``ai-tools config`` and
``/ai-standardize`` in Claude Code.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import click

_DIVIDER = click.style("─" * 50, fg="bright_black")

# ---------------------------------------------------------------------------
# Project type registry
# ---------------------------------------------------------------------------


@dataclass
class ProjectType:
    id: str
    name: str
    description: str
    prereqs: list[str]
    scaffold: Callable[[str, str, Path], int]
    # True = the scaffolding tool creates the target directory itself.
    # False = we create it and run the tool inside it.
    tool_creates_dir: bool = True
    # True = the tool is interactive on its own; skip our detail prompts.
    own_wizard: bool = False


@dataclass
class InstallHint:
    summary: str
    docs: str
    commands: dict[str, str]  # platform key -> one-liner install command


_INSTALL_HINTS: dict[str, InstallHint] = {
    "uv": InstallHint(
        summary="Python packaging and virtualenv manager",
        docs="https://docs.astral.sh/uv/getting-started/installation/",
        commands={
            "darwin": "brew install uv",
            "linux": "curl -LsSf https://astral.sh/uv/install.sh | sh",
            "win32": 'powershell -c "irm https://astral.sh/uv/install.ps1 | iex"',
        },
    ),
    "npm": InstallHint(
        summary="Node.js package manager (ships with Node.js)",
        docs="https://nodejs.org/en/download",
        commands={
            "darwin": "brew install node",
            "linux": "see https://nodejs.org/en/download/package-manager",
            "win32": "winget install OpenJS.NodeJS",
        },
    ),
    "npx": InstallHint(
        summary="Node.js package runner (ships with npm / Node.js)",
        docs="https://nodejs.org/en/download",
        commands={
            "darwin": "brew install node",
            "linux": "see https://nodejs.org/en/download/package-manager",
            "win32": "winget install OpenJS.NodeJS",
        },
    ),
    "cdk": InstallHint(
        summary="AWS Cloud Development Kit CLI (requires npm)",
        docs="https://docs.aws.amazon.com/cdk/v2/guide/getting_started.html",
        commands={
            "darwin": "npm install -g aws-cdk",
            "linux": "npm install -g aws-cdk",
            "win32": "npm install -g aws-cdk",
        },
    ),
    "sam": InstallHint(
        summary="AWS Serverless Application Model CLI",
        docs=(
            "https://docs.aws.amazon.com/serverless-application-model/"
            "latest/developerguide/install-sam-cli.html"
        ),
        commands={
            "darwin": "brew install aws-sam-cli",
            "linux": (
                "see https://docs.aws.amazon.com/serverless-application-model/"
                "latest/developerguide/install-sam-cli.html"
            ),
            "win32": "winget install Amazon.SAM-CLI",
        },
    ),
    "git": InstallHint(
        summary="Git version control",
        docs="https://git-scm.com/downloads",
        commands={
            "darwin": "brew install git",
            "linux": "apt install git   (or: dnf install git)",
            "win32": "winget install Git.Git",
        },
    ),
}


_PLATFORM_LABELS = {"darwin": "macOS", "linux": "Linux", "win32": "Windows"}


def _run(cmd: list[str], cwd: Path | None = None) -> int:
    """Run a command with inherited stdio so interactive tools work."""
    # On Windows, tools like npm/npx/cdk/sam are .cmd wrappers that CreateProcess
    # cannot execute directly; route them through cmd.exe /c.  Avoid shell=True
    # so we don't trigger shell-injection scanners and keep args as a list.
    final_cmd = cmd
    if sys.platform == "win32":
        resolved = shutil.which(cmd[0])
        if resolved and resolved.lower().endswith((".cmd", ".bat")):
            final_cmd = ["cmd.exe", "/c", *cmd]
    return subprocess.run(final_cmd, cwd=str(cwd) if cwd else None).returncode


# --- Scaffolding implementations ---


def _scaffold_python_lib(name: str, _desc: str, target: Path) -> int:
    # uv init --lib <name> run in the parent creates target/
    return _run(["uv", "init", "--lib", name], cwd=target.parent)


def _scaffold_npm_lib(name: str, _desc: str, target: Path) -> int:
    target.mkdir(parents=True, exist_ok=True)
    rc = _run(["npm", "init", "-y"], cwd=target)
    return rc


def _scaffold_sam(_name: str, _desc: str, target: Path) -> int:
    # SAM has its own wizard; run it in the parent directory.
    return _run(["sam", "init"], cwd=target.parent)


def _scaffold_cdk_python(_name: str, _desc: str, target: Path) -> int:
    target.mkdir(parents=True, exist_ok=True)
    return _run(["cdk", "init", "app", "--language", "python"], cwd=target)


def _scaffold_cdk_ts(_name: str, _desc: str, target: Path) -> int:
    target.mkdir(parents=True, exist_ok=True)
    return _run(["cdk", "init", "app", "--language", "typescript"], cwd=target)


def _scaffold_nextjs(name: str, _desc: str, target: Path) -> int:
    rc = _run(
        [
            "npx",
            "create-next-app@latest",
            name,
            "--typescript",
            "--tailwind",
            "--eslint",
            "--app",
            "--src-dir",
            "--import-alias",
            "@/*",
            "--use-npm",
        ],
        cwd=target.parent,
    )
    if rc != 0:
        return rc
    # Post-scaffold setup
    project_dir = target if target.is_dir() else target.parent / name

    # Install prettier (CI and pre-commit require it; create-next-app omits it)
    _run(["npm", "install", "-D", "prettier"], cwd=project_dir)

    # Initialize shadcn/ui component library
    shadcn_rc = _run(["npx", "shadcn@latest", "init", "--defaults"], cwd=project_dir)
    if shadcn_rc != 0:
        click.echo(
            click.style(
                "\nshadcn/ui init failed (non-fatal). Run manually later:\n"
                "  npx shadcn@latest init --defaults",
                fg="yellow",
            )
        )
    return 0


PROJECT_TYPES: list[ProjectType] = [
    ProjectType(
        id="python-lib",
        name="Python library (PyPI)",
        description="Installable Python package with src layout, uv toolchain",
        prereqs=["uv"],
        scaffold=_scaffold_python_lib,
    ),
    ProjectType(
        id="npm-lib",
        name="npm library",
        description="JavaScript/TypeScript package published to npm",
        prereqs=["npm"],
        scaffold=_scaffold_npm_lib,
        tool_creates_dir=False,
    ),
    ProjectType(
        id="sam",
        name="AWS SAM deployment",
        description="Serverless Application Model -- Lambda, API Gateway, etc.",
        prereqs=["sam"],
        scaffold=_scaffold_sam,
        own_wizard=True,
    ),
    ProjectType(
        id="cdk-python",
        name="AWS CDK (Python)",
        description="Cloud Development Kit infrastructure-as-code, Python",
        prereqs=["cdk"],
        scaffold=_scaffold_cdk_python,
        tool_creates_dir=False,
    ),
    ProjectType(
        id="cdk-ts",
        name="AWS CDK (TypeScript)",
        description="Cloud Development Kit infrastructure-as-code, TypeScript",
        prereqs=["cdk", "npm"],
        scaffold=_scaffold_cdk_ts,
        tool_creates_dir=False,
    ),
    ProjectType(
        id="nextjs",
        name="Next.js app",
        description="Next.js with TypeScript, Tailwind CSS, shadcn/ui, App Router",
        prereqs=["npx"],
        scaffold=_scaffold_nextjs,
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _missing_prereqs(pt: ProjectType) -> list[str]:
    return [p for p in pt.prereqs if shutil.which(p) is None]


def _print_install_hint(tool: str) -> None:
    """Print an install hint for a single missing tool."""
    hint = _INSTALL_HINTS.get(tool)
    click.echo(f"  {click.style(tool, bold=True, fg='yellow')}")
    if hint is None:
        click.echo("      (no install hint available -- check the tool's documentation)")
        return
    click.echo(f"      {hint.summary}")
    platform_cmd = hint.commands.get(sys.platform)
    if platform_cmd:
        label = _PLATFORM_LABELS.get(sys.platform, sys.platform)
        click.echo(f"      {label}: {platform_cmd}")
    click.echo(f"      Docs:    {hint.docs}")


def _slugify(name: str) -> str:
    """Lower-case, replace spaces and underscores with hyphens."""
    return name.strip().lower().replace(" ", "-").replace("_", "-")


_TYPE_IDS = {pt.id for pt in PROJECT_TYPES}


def _find_type(type_id: str) -> ProjectType | None:
    """Look up a project type by its id string."""
    for pt in PROJECT_TYPES:
        if pt.id == type_id:
            return pt
    return None


# ---------------------------------------------------------------------------
# Wizard
# ---------------------------------------------------------------------------


def _print_header() -> None:
    click.echo("")
    click.echo(click.style("ai-tools init", bold=True) + "  --  new project wizard")
    click.echo(_DIVIDER)
    click.echo("")


def _select_project_type() -> ProjectType:
    click.echo(click.style("Select project type:", bold=True))
    click.echo("")
    for i, pt in enumerate(PROJECT_TYPES, 1):
        missing = _missing_prereqs(pt)
        suffix = click.style(f"  (needs: {', '.join(missing)})", fg="yellow") if missing else ""
        click.echo(f"  {click.style(str(i) + ')', bold=True)}  {pt.name}{suffix}")
        click.echo(f"       {click.style(pt.description, fg='bright_black')}")
    click.echo("")
    choice: int = click.prompt("Enter number", type=click.IntRange(1, len(PROJECT_TYPES)))
    return PROJECT_TYPES[choice - 1]


def _collect_details(pt: ProjectType, path_override: Path | None = None) -> tuple[str, str, Path]:
    """Prompt for project name, description and target directory."""
    click.echo("")
    click.echo(click.style("Project details:", bold=True))
    click.echo("")

    name_default = path_override.name if path_override else None
    raw_name = click.prompt("  Name", default=name_default)
    name = _slugify(raw_name)
    if name != raw_name:
        click.echo(f"       (normalised to: {click.style(name, bold=True)})")

    desc = click.prompt("  Description", default="")

    if path_override is not None:
        target = path_override
    else:
        default_target = Path.cwd() / name
        raw_target = click.prompt("  Create in", default=str(default_target))
        target = Path(raw_target).expanduser().resolve()

    return name, desc, target


def _confirm_plan(pt: ProjectType, name: str, target: Path) -> bool:
    click.echo("")
    click.echo(click.style("Ready to scaffold:", bold=True))
    click.echo("")
    click.echo(f"  Type     {pt.name}")
    click.echo(f"  Name     {name}")
    click.echo(f"  Location {target}")
    click.echo(f"  Tools    {', '.join(pt.prereqs)}")
    click.echo("")
    return click.confirm("Proceed?", default=True)


def _git_init(project_dir: Path) -> None:
    """Init git repo and make an initial commit if one doesn't exist."""
    git_dir = project_dir / ".git"
    if git_dir.is_dir():
        return
    if not click.confirm("\nInitialize git repository?", default=True):
        return
    _run(["git", "init"], cwd=project_dir)
    _run(["git", "add", "."], cwd=project_dir)
    _run(["git", "commit", "-m", "chore: initial scaffolding"], cwd=project_dir)


def _print_next_steps(project_dir: Path) -> None:
    rel = os.path.relpath(project_dir)
    click.echo("")
    click.echo(click.style("Project created!", fg="green", bold=True))
    click.echo("")
    click.echo(click.style("Next steps:", bold=True))
    click.echo(f"  1.  cd {rel}")
    click.echo(f"  2.  {click.style('ai-tools config', bold=True)}  -- set up IDE and GitHub token")
    click.echo(
        f"  3.  {click.style('/ai-standardize', bold=True)}  -- apply quality gates in Claude Code"
    )
    click.echo(
        f"  4.  {click.style('/ai-submit-work', bold=True)}  -- commit, push and open PR when ready"
    )
    click.echo("")


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


@click.command("init")
@click.argument("path", default=None, required=False)
@click.option(
    "--type",
    "type_id",
    type=click.Choice(sorted(_TYPE_IDS), case_sensitive=False),
    default=None,
    help="Project type (skips interactive selection).",
)
@click.option(
    "--name",
    "proj_name",
    default=None,
    help="Project name (skips interactive prompt).",
)
@click.option(
    "-y",
    "--yes",
    is_flag=True,
    default=False,
    help="Skip confirmation prompts.",
)
@click.option(
    "--no-git-init",
    is_flag=True,
    default=False,
    help="Skip git init (useful when called from /ai-new-project).",
)
@click.pass_context
def init(
    ctx: click.Context,
    path: str | None,
    type_id: str | None,
    proj_name: str | None,
    yes: bool,
    no_git_init: bool,
) -> None:
    """New project scaffold wizard.

    Guides you through selecting a project type (Python library, npm package,
    SAM, CDK, Next.js) and running the appropriate toolchain to create
    the project structure.  After scaffolding, prints next-step instructions.

    Optional PATH sets the target directory (use . for the current directory).
    When omitted, you are prompted for a name and the project is created in a
    subdirectory of the current directory.

    Non-interactive mode: pass --type, --name, and --yes to skip all prompts.
    """
    path_override = Path(path).expanduser().resolve() if path else None

    _print_header()

    # 1. Pick project type
    if type_id:
        pt = _find_type(type_id)
        if pt is None:
            click.echo(click.style(f"Unknown project type: {type_id}", fg="red"))
            ctx.exit(1)
            return
        click.echo(f"  Selected: {click.style(pt.name, bold=True)}")
    else:
        pt = _select_project_type()
        click.echo("")
        click.echo(f"  Selected: {click.style(pt.name, bold=True)}")

    # 2. Check prereqs
    missing = _missing_prereqs(pt)
    if missing:
        click.echo("")
        click.echo(
            click.style(f"Missing required tool(s): {', '.join(missing)}", fg="red", bold=True)
        )
        click.echo("")
        for tool in missing:
            _print_install_hint(tool)
            click.echo("")
        click.echo("Install the missing tool(s) and run this wizard again.")
        ctx.exit(1)
        return

    # 3a. Tools with their own wizard (e.g. SAM): run directly, no detail prompts
    if pt.own_wizard:
        click.echo("")
        click.echo(click.style(pt.description, fg="bright_black"))
        click.echo(
            "\nThis tool has its own interactive wizard.  "
            "Launching it now in the current directory.\n"
        )
        rc = pt.scaffold("", "", path_override or Path.cwd())
        if rc != 0:
            click.echo(click.style(f"\nScaffolding exited with code {rc}.", fg="yellow"))
        return

    # 3b. Collect project details (or use CLI args)
    if proj_name and (path_override or yes):
        name = _slugify(proj_name)
        target = path_override if path_override else Path.cwd() / name
        desc = ""
    else:
        name, desc, target = _collect_details(pt, path_override)

    # 4. Confirm
    if not yes and not _confirm_plan(pt, name, target):
        click.echo("Aborted.")
        return

    # 5. Check target directory
    if target.exists() and any(target.iterdir()):
        click.echo(click.style(f"\nDirectory already exists and is not empty: {target}", fg="red"))
        if not yes and not click.confirm("Continue anyway?", default=False):
            return

    # 6. Run scaffolding
    click.echo("")
    click.echo(_DIVIDER)
    click.echo(click.style("Scaffolding...", bold=True))
    click.echo("")

    rc = pt.scaffold(name, desc, target)
    if rc != 0:
        click.echo(click.style(f"\nScaffolding command exited with code {rc}.", fg="red"))
        click.echo("Check the output above for details.")
        return

    # 7. Git init (when not already handled by scaffolding tool)
    if not no_git_init:
        if yes:
            git_dir = target / ".git"
            if not git_dir.is_dir():
                _run(["git", "init"], cwd=target)
                _run(["git", "add", "."], cwd=target)
                _run(["git", "commit", "-m", "chore: initial scaffolding"], cwd=target)
        else:
            _git_init(target)

    # 8. Next steps
    _print_next_steps(target)
