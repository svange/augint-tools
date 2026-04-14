"""Individual IDEA setup steps.

Each step is a pure function that reads/writes project XML and returns a
:class:`StepResult`. Steps never prompt or print — the CLI layer owns all
user-facing I/O so the same functions can be composed from an interactive
wizard, a non-interactive agent invocation, or unit tests.
"""

from __future__ import annotations

import glob
import os
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

from augint_tools.ide.detect import parse_git_remote
from augint_tools.ide.xml import (
    find_component,
    get_or_create_component,
    minimal_application_xml,
    minimal_project_xml,
    read_xml,
    write_xml,
)

ALWAYS_EXCLUDE = [
    "dist",
    "build",
    "htmlcov",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    ".tox",
]


@dataclass
class StepResult:
    """Outcome of a single setup step.

    ``status`` is one of ``"ok"``, ``"skipped"``, ``"action-required"``,
    or ``"error"``. ``missing_inputs`` enumerates keys the caller must supply
    before re-running; ``next_action`` is a human-readable instruction.
    """

    name: str
    status: str
    message: str
    missing_inputs: list[str] = field(default_factory=list)
    next_action: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "missing_inputs": self.missing_inputs,
            "next_action": self.next_action,
            "details": self.details,
        }


def _ok(name: str, message: str, **details: Any) -> StepResult:
    return StepResult(name=name, status="ok", message=message, details=dict(details))


def _skipped(name: str, message: str, **details: Any) -> StepResult:
    return StepResult(name=name, status="skipped", message=message, details=dict(details))


def _error(name: str, message: str) -> StepResult:
    return StepResult(name=name, status="error", message=message)


def _action_required(
    name: str,
    message: str,
    missing_inputs: list[str] | None = None,
    next_action: str | None = None,
) -> StepResult:
    return StepResult(
        name=name,
        status="action-required",
        message=message,
        missing_inputs=missing_inputs or [],
        next_action=next_action,
    )


# ---------------------------------------------------------------------------
# Step 1: Terminal → right panel
# ---------------------------------------------------------------------------


def step_terminal_right(workspace_path: str, dry_run: bool = False) -> StepResult:
    name = "terminal"
    tree, root = read_xml(workspace_path)
    if tree is None or root is None:
        tree, root = minimal_project_xml()

    comp = get_or_create_component(root, "ToolWindowManager")
    layout = comp.find("layout")
    if layout is None:
        layout = ET.SubElement(comp, "layout")

    existing = layout.find('.//window_info[@id="Terminal"]')
    if existing is not None:
        if existing.get("anchor") == "right" and existing.get("side_tool") == "false":
            return _skipped(name, "Terminal already anchored to right")
        existing.set("anchor", "right")
        existing.set("side_tool", "false")
    else:
        ET.SubElement(
            layout,
            "window_info",
            id="Terminal",
            anchor="right",
            side_tool="false",
            order="7",
        )

    write_xml(tree, workspace_path, dry_run)
    return _ok(name, "Terminal anchor set to right")


# ---------------------------------------------------------------------------
# Step 2: Module SDK (.iml)
# ---------------------------------------------------------------------------


def step_module_sdk(iml_path: str | None, sdk_name: str, dry_run: bool = False) -> StepResult:
    name = "module_sdk"
    if iml_path is None:
        return _error(name, "No .iml file found in project root")

    tree, root = read_xml(iml_path)
    if tree is None or root is None:
        return _error(name, f"Could not read {iml_path}")

    comp = root.find('.//component[@name="NewModuleRootManager"]')
    if comp is None:
        return _error(name, f"NewModuleRootManager not found in {iml_path}")

    existing = comp.find('.//orderEntry[@type="jdk"]')
    if existing is not None:
        if existing.get("jdkName") == sdk_name and existing.get("jdkType") == "Python SDK":
            return _skipped(name, f"Module SDK already set to '{sdk_name}'")
        existing.set("jdkName", sdk_name)
        existing.set("jdkType", "Python SDK")
    else:
        ET.SubElement(comp, "orderEntry", type="jdk", jdkName=sdk_name, jdkType="Python SDK")

    write_xml(tree, iml_path, dry_run)
    return _ok(name, f"Module SDK set to '{sdk_name}'", sdk_name=sdk_name)


# ---------------------------------------------------------------------------
# Step 3: Project structure (.iml)
# ---------------------------------------------------------------------------


def step_project_structure(
    iml_path: str | None,
    project_dir: str,
    project_name: str,
    dry_run: bool = False,
) -> StepResult:
    name = "structure"
    if iml_path is None:
        return _error(name, "No .iml file found in project root")

    tree, root = read_xml(iml_path)
    if tree is None or root is None:
        return _error(name, f"Could not read {iml_path}")

    comp = root.find('.//component[@name="NewModuleRootManager"]')
    if comp is None:
        return _error(name, "NewModuleRootManager not found")

    content = comp.find('content[@url="file://$MODULE_DIR$"]')
    if content is None:
        return _error(name, "<content> element not found in .iml")

    def _url(rel: str) -> str:
        return f"file://$MODULE_DIR$/{rel}"

    def _has_source(rel: str) -> bool:
        return content.find(f'sourceFolder[@url="{_url(rel)}"]') is not None

    def _has_exclude(rel: str) -> bool:
        return content.find(f'excludeFolder[@url="{_url(rel)}"]') is not None

    added_sources: list[str] = []
    added_tests: list[str] = []
    added_excludes: list[str] = []

    source_candidates = ["src", "scripts"]
    pkg_init = os.path.join(project_dir, project_name, "__init__.py")
    if os.path.isdir(os.path.join(project_dir, project_name)) and os.path.exists(pkg_init):
        source_candidates.insert(0, project_name)

    for d in source_candidates:
        if not os.path.isdir(os.path.join(project_dir, d)):
            continue
        if not _has_source(d):
            added_sources.append(d)

    for d in ["tests", "test"]:
        if not os.path.isdir(os.path.join(project_dir, d)):
            continue
        if not _has_source(d):
            added_tests.append(d)

    exclude_candidates = list(ALWAYS_EXCLUDE)
    for p in glob.glob(os.path.join(project_dir, "*.egg-info")):
        if os.path.isdir(p):
            exclude_candidates.append(os.path.basename(p))

    for d in exclude_candidates:
        if not _has_exclude(d):
            added_excludes.append(d)

    if not added_sources and not added_tests and not added_excludes:
        return _skipped(name, "Project structure already configured")

    existing_excludes = content.findall("excludeFolder")
    insert_idx = list(content).index(existing_excludes[0]) if existing_excludes else len(content)

    offset = 0
    for d in added_sources:
        el = ET.Element("sourceFolder", url=_url(d), isTestSource="false")
        content.insert(insert_idx + offset, el)
        offset += 1

    for d in added_tests:
        el = ET.Element("sourceFolder", url=_url(d), isTestSource="true")
        content.insert(insert_idx + offset, el)
        offset += 1

    for d in added_excludes:
        ET.SubElement(content, "excludeFolder", url=_url(d))

    write_xml(tree, iml_path, dry_run)
    msg = (
        f"added {len(added_sources)} source, {len(added_tests)} test, "
        f"{len(added_excludes)} excluded roots"
    )
    return _ok(
        name,
        msg,
        added_sources=added_sources,
        added_tests=added_tests,
        added_excludes=added_excludes,
    )


# ---------------------------------------------------------------------------
# Step 4: Project SDK (misc.xml)
# ---------------------------------------------------------------------------


def step_project_sdk(misc_path: str, sdk_name: str, dry_run: bool = False) -> StepResult:
    name = "project_sdk"
    tree, root = read_xml(misc_path)
    if tree is None or root is None:
        return _error(name, f"Could not read {misc_path}")

    comp = find_component(root, "ProjectRootManager")
    if comp is None:
        return _error(name, "ProjectRootManager not found in misc.xml")

    if comp.get("project-jdk-name") == sdk_name and comp.get("project-jdk-type") == "Python SDK":
        return _skipped(name, f"Project SDK already set to '{sdk_name}'")

    comp.set("project-jdk-name", sdk_name)
    comp.set("project-jdk-type", "Python SDK")
    write_xml(tree, misc_path, dry_run)
    return _ok(name, f"Project SDK set to '{sdk_name}'", sdk_name=sdk_name)


# ---------------------------------------------------------------------------
# Step 5: GitHub Tasks server (workspace.xml)
# ---------------------------------------------------------------------------


def step_github_tasks(
    workspace_path: str,
    project_dir: str,
    gh_token: str | None,
    dry_run: bool = False,
) -> StepResult:
    name = "github_tasks"
    remote = parse_git_remote(project_dir)
    if remote is None:
        return _skipped(name, "No GitHub remote origin found in .git/config")

    owner, repo, canonical_url = remote

    tree, root = read_xml(workspace_path)
    if tree is None or root is None:
        tree, root = minimal_project_xml()

    task_mgr = get_or_create_component(root, "TaskManager")
    servers = task_mgr.find("servers")
    if servers is None:
        servers = ET.SubElement(task_mgr, "servers")

    for server in servers.findall("server"):
        if server.get("url") == canonical_url:
            return _skipped(name, f"GitHub server already configured for {canonical_url}")

    if not gh_token:
        return _action_required(
            name,
            f"GH_TOKEN not found; cannot configure Tasks server for {canonical_url}",
            missing_inputs=["GH_TOKEN"],
            next_action=(
                "Create a token at "
                "https://github.com/settings/tokens/new?scopes=repo,read:user&description=IntelliJ+Tasks "
                "and add GH_TOKEN=<token> to .env"
            ),
        )

    server_el = ET.SubElement(
        servers,
        "server",
        id=str(uuid.uuid4()),
        serverClass="com.intellij.tasks.github.GitHubRepositoryType",
        url=canonical_url,
        shared="false",
        shouldFormatCommitMessage="false",
    )
    ET.SubElement(server_el, "option", name="token", value=gh_token)
    ET.SubElement(server_el, "option", name="repoAuthor", value=owner)
    ET.SubElement(server_el, "option", name="repoName", value=repo)

    write_xml(tree, workspace_path, dry_run)
    return _ok(
        name,
        f"GitHub Tasks server added for {canonical_url}",
        url=canonical_url,
        owner=owner,
        repo=repo,
    )


# ---------------------------------------------------------------------------
# Step 6: SDK registration (jdk.table.xml)
# ---------------------------------------------------------------------------


def step_jdk_table(
    jb_options_dir: str | None,
    sdk_name: str,
    full_version: str,
    win_python_path: str | None,
    win_venv_path: str | None,
    dry_run: bool = False,
) -> StepResult:
    name = "jdk_table"

    if jb_options_dir is None:
        return _action_required(
            name,
            "JetBrains config dir not found",
            missing_inputs=["jb_options_dir"],
            next_action=(
                "Open IntelliJ IDEA once to create the config directory, or install IntelliJ "
                "IDEA, then re-run. Alternatively register the SDK manually: "
                "File -> Project Structure -> SDKs -> + -> Python SDK"
            ),
        )

    if win_python_path is None:
        return _action_required(
            name,
            "Windows project path could not be determined",
            missing_inputs=["windows_project_dir"],
            next_action=(
                'Pass --windows-project-dir "C:/Users/you/projects/MYPROJECT" '
                "(or run from a path that wslpath can resolve)"
            ),
        )

    jdk_table_path = os.path.join(jb_options_dir, "jdk.table.xml")
    tree, root = read_xml(jdk_table_path)
    if tree is None or root is None:
        tree, root = minimal_application_xml()
        ET.SubElement(root, "component", name="ProjectJdkTable")

    table = find_component(root, "ProjectJdkTable")
    if table is None:
        table = ET.SubElement(root, "component", name="ProjectJdkTable")

    for jdk in table.findall("jdk"):
        name_el = jdk.find("name")
        if name_el is not None and name_el.get("value") == sdk_name:
            return _skipped(name, f"SDK '{sdk_name}' already in jdk.table.xml")

    jdk_el = ET.SubElement(table, "jdk", version="2")
    ET.SubElement(jdk_el, "name", value=sdk_name)
    ET.SubElement(jdk_el, "type", value="Python SDK")
    ET.SubElement(jdk_el, "version", value=f"Python {full_version}")
    ET.SubElement(jdk_el, "homePath", value=win_python_path)

    roots_el = ET.SubElement(jdk_el, "roots")
    cp = ET.SubElement(roots_el, "classPath")
    ET.SubElement(cp, "root", type="composite")
    sp = ET.SubElement(roots_el, "sourcePath")
    ET.SubElement(sp, "root", type="composite")

    ET.SubElement(jdk_el, "additional", HOMEPATH=win_venv_path or win_python_path)

    write_xml(tree, jdk_table_path, dry_run)
    return _ok(
        name,
        f"SDK '{sdk_name}' registered in {jdk_table_path}",
        sdk_name=sdk_name,
        jdk_table_path=jdk_table_path,
    )
