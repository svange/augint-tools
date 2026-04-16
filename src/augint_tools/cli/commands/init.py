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


def _run(cmd: list[str], cwd: Path | None = None) -> int:
    """Run a command with inherited stdio so interactive tools work."""
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None).returncode


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


def _scaffold_react(name: str, _desc: str, target: Path) -> int:
    return _run(["npx", "create-react-app", name], cwd=target.parent)


def _scaffold_nextjs(name: str, _desc: str, target: Path) -> int:
    return _run(["npx", "create-next-app@latest", name], cwd=target.parent)


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
        id="react",
        name="React app",
        description="React single-page application (create-react-app)",
        prereqs=["npx"],
        scaffold=_scaffold_react,
    ),
    ProjectType(
        id="nextjs",
        name="Next.js app",
        description="Next.js full-stack React framework",
        prereqs=["npx"],
        scaffold=_scaffold_nextjs,
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _missing_prereqs(pt: ProjectType) -> list[str]:
    return [p for p in pt.prereqs if shutil.which(p) is None]


def _slugify(name: str) -> str:
    """Lower-case, replace spaces and underscores with hyphens."""
    return name.strip().lower().replace(" ", "-").replace("_", "-")


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


def _collect_details(pt: ProjectType) -> tuple[str, str, Path]:
    """Prompt for project name, description and target directory."""
    click.echo("")
    click.echo(click.style("Project details:", bold=True))
    click.echo("")

    raw_name = click.prompt("  Name")
    name = _slugify(raw_name)
    if name != raw_name:
        click.echo(f"       (normalised to: {click.style(name, bold=True)})")

    desc = click.prompt("  Description", default="")

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
@click.pass_context
def init(ctx: click.Context) -> None:
    """New project scaffold wizard.

    Guides you through selecting a project type (Python library, npm package,
    SAM, CDK, React, Next.js) and running the appropriate toolchain to create
    the project structure.  After scaffolding, prints next-step instructions.
    """
    _print_header()

    # 1. Pick project type
    pt = _select_project_type()
    click.echo("")
    click.echo(f"  Selected: {click.style(pt.name, bold=True)}")

    # 2. Check prereqs
    missing = _missing_prereqs(pt)
    if missing:
        click.echo("")
        click.echo(click.style(f"Required tool(s) not found: {', '.join(missing)}", fg="red"))
        click.echo("Install them and try again.")
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
        rc = pt.scaffold("", "", Path.cwd())
        if rc != 0:
            click.echo(click.style(f"\nScaffolding exited with code {rc}.", fg="yellow"))
        return

    # 3b. Collect project details
    name, desc, target = _collect_details(pt)

    # 4. Confirm
    if not _confirm_plan(pt, name, target):
        click.echo("Aborted.")
        return

    # 5. Check target directory
    if target.exists() and any(target.iterdir()):
        click.echo(click.style(f"\nDirectory already exists and is not empty: {target}", fg="red"))
        if not click.confirm("Continue anyway?", default=False):
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
    _git_init(target)

    # 8. Next steps
    _print_next_steps(target)
