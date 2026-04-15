"""Auto-detect key project files and generate mnemonic bookmark assignments.

IntelliJ 2022.1+ stores bookmarks in a ``BookmarksManager`` component inside a
*product workspace file* located at ``{jb_config_root}/workspace/{ksuid}.xml``.
The KSUID is assigned when the project is first opened and cannot be predicted
ahead of time, so we locate it by searching for XML files that reference the
project's path.
"""

from __future__ import annotations

import glob
import os
import xml.etree.ElementTree as ET  # nosemgrep: python.lang.security.use-defused-xml.use-defused-xml
from dataclasses import dataclass
from typing import Any


@dataclass
class BookmarkSlot:
    """A single file/mnemonic assignment."""

    mnemonic: str  # "DIGIT_1" .. "DIGIT_0"
    label: str  # human-friendly label, e.g. "Project config"
    path: str  # absolute filesystem path to the file
    rel: str  # relative path from project root (for display)


# ---------------------------------------------------------------------------
# Mnemonic candidate definitions — order = priority within each slot.
# Evaluated top-to-bottom; first match for each slot wins.
# ---------------------------------------------------------------------------

_CANDIDATES: list[tuple[str, str, list[str]]] = [
    # (mnemonic_digit, label, glob-patterns relative to project root)
    ("1", "Project config", ["pyproject.toml", "package.json"]),
    ("2", "AI context", ["CLAUDE.md"]),
    (
        "3",
        "Entry point",
        [
            "src/*/cli/__main__.py",
            "src/*/__main__.py",
            "src/*/cli.py",
            "src/*/main.py",
            "src/*/app.py",
            "app.py",
            "main.py",
            "manage.py",
            # React / Next
            "src/App.tsx",
            "src/app/page.tsx",
            "src/index.tsx",
            "src/main.tsx",
            "pages/index.tsx",
            # TypeScript / Node
            "src/index.ts",
            "src/main.ts",
            "index.ts",
            # CDK
            "bin/*.ts",
        ],
    ),
    ("4", "Environment", [".env", ".env.example"]),
    (
        "5",
        "CI workflow",
        [
            ".github/workflows/ci.yml",
            ".github/workflows/ci.yaml",
            ".github/workflows/main.yml",
            ".github/workflows/main.yaml",
            ".github/workflows/build.yml",
            ".github/workflows/build.yaml",
            ".github/workflows/*.yml",
            ".github/workflows/*.yaml",
        ],
    ),
    ("6", "README", ["README.md", "readme.md"]),
    (
        "7",
        "AI agents",
        [
            "AGENTS.md",
            ".claude/settings.json",
            ".claude/settings.local.json",
        ],
    ),
    ("8", "Pre-commit", [".pre-commit-config.yaml"]),
    (
        "9",
        "IaC / deploy",
        [
            "cdk.json",
            "template.yaml",
            "template.yml",
            "samconfig.toml",
            "Dockerfile",
            "docker-compose.yml",
            "docker-compose.yaml",
        ],
    ),
    (
        "0",
        "Test config",
        [
            "tests/conftest.py",
            "conftest.py",
            "jest.config.ts",
            "jest.config.js",
            "vitest.config.ts",
        ],
    ),
]


def discover_bookmarks(project_dir: str) -> list[BookmarkSlot]:
    """Scan ``project_dir`` and return a list of bookmark assignments.

    Each slot maps one mnemonic digit to the first file that matches its
    candidate patterns. Slots with no matches are omitted.
    """
    slots: list[BookmarkSlot] = []
    for digit, label, patterns in _CANDIDATES:
        hit = _first_match(project_dir, patterns)
        if hit is None:
            continue
        rel = os.path.relpath(hit, project_dir)
        mnemonic = f"DIGIT_{digit}"
        slots.append(BookmarkSlot(mnemonic=mnemonic, label=label, path=hit, rel=rel))
    return slots


def _first_match(project_dir: str, patterns: list[str]) -> str | None:
    """Return the first existing file matching any of ``patterns``."""
    for pattern in patterns:
        matches = glob.glob(os.path.join(project_dir, pattern))
        if matches:
            return matches[0]
    return None


# ---------------------------------------------------------------------------
# Product workspace file discovery
# ---------------------------------------------------------------------------


def find_product_workspace_file(
    jb_options_dir: str | None,
    project_path_windows: str | None,
    project_name: str | None = None,
) -> str | None:
    """Locate the product workspace XML that corresponds to the project.

    Searches ``{jb_config_root}/workspace/*.xml`` for files that contain the
    project path (or name as fallback). Returns the path to the matching file,
    or ``None`` if nothing is found.
    """
    if jb_options_dir is None:
        return None

    config_root = os.path.dirname(jb_options_dir)  # parent of options/
    workspace_dir = os.path.join(config_root, "workspace")
    if not os.path.isdir(workspace_dir):
        return None

    xml_files = glob.glob(os.path.join(workspace_dir, "*.xml"))
    if not xml_files:
        return None

    # Build search needles — normalise slashes so Windows/Unix paths match
    needles: list[str] = []
    if project_path_windows:
        needles.append(project_path_windows.replace("\\", "/"))
    if project_name:
        needles.append(project_name)

    if not needles:
        return None

    for xml_path in xml_files:
        try:
            with open(xml_path, encoding="utf-8", errors="replace") as f:
                content = f.read(64_000)  # read first 64k — enough for header
            for needle in needles:
                if needle in content:
                    return xml_path
        except OSError:
            continue

    return None


# ---------------------------------------------------------------------------
# XML writing
# ---------------------------------------------------------------------------

# Provider class for file-level bookmarks (no specific line).
_FILE_PROVIDER = "com.intellij.ide.bookmark.providers.LineBookmarkProvider"


def build_bookmarks_xml(
    slots: list[BookmarkSlot],
    project_dir: str,
    group_name: str | None = None,
) -> ET.Element:
    """Build a ``BookmarksManager`` component element from bookmark slots.

    Paths are encoded as ``file://$PROJECT_DIR$/relative/path``.
    """
    component = ET.Element("component", name="BookmarksManager")
    groups_option = ET.SubElement(component, "option", name="groups")
    group = ET.SubElement(groups_option, "GroupState")
    ET.SubElement(group, "option", name="bookmarks")
    ET.SubElement(group, "option", name="isDefault", value="true")
    ET.SubElement(group, "option", name="name", value=group_name or "Bookmarks")

    bookmarks = group.find('option[@name="bookmarks"]')
    assert bookmarks is not None  # just created above

    for slot in slots:
        rel = os.path.relpath(slot.path, project_dir).replace("\\", "/")
        url = f"file://$PROJECT_DIR$/{rel}"

        bstate = ET.SubElement(bookmarks, "BookmarkState")
        attrs = ET.SubElement(bstate, "attributes")
        ET.SubElement(attrs, "entry", key="url", value=url)
        ET.SubElement(attrs, "entry", key="line", value="0")
        ET.SubElement(bstate, "option", name="provider", value=_FILE_PROVIDER)
        ET.SubElement(bstate, "option", name="type", value=slot.mnemonic)

    return component


def inject_bookmark_group(
    workspace_file: str,
    slots: list[BookmarkSlot],
    project_dir: str,
    group_name: str,
    dry_run: bool = False,
    *,
    set_default: bool = True,
) -> dict[str, Any]:
    """Upsert one bookmark list inside a native ``BookmarksManager`` component.

    Existing lists are preserved. If ``set_default`` is true, this group is
    marked as default and all sibling groups are marked non-default.
    """
    from augint_tools.ide.xml import read_xml, write_xml

    tree, root = read_xml(workspace_file)
    if tree is None or root is None:
        return {"action": "error", "reason": f"Could not read {workspace_file}"}

    component = root.find('.//component[@name="BookmarksManager"]')
    if component is None:
        component = ET.SubElement(root, "component", name="BookmarksManager")

    groups_option = component.find('option[@name="groups"]')
    if groups_option is None:
        groups_option = ET.SubElement(component, "option", name="groups")

    group_component = build_bookmarks_xml(slots, project_dir, group_name=group_name)
    new_group = group_component.find('./option[@name="groups"]/GroupState')
    assert new_group is not None  # guaranteed by build_bookmarks_xml

    replaced = False
    for existing_group in list(groups_option.findall("GroupState")):
        name_opt = existing_group.find('./option[@name="name"]')
        if name_opt is not None and name_opt.get("value") == group_name:
            groups_option.remove(existing_group)
            replaced = True
            break

    if set_default:
        for sibling in groups_option.findall("GroupState"):
            default_opt = sibling.find('./option[@name="isDefault"]')
            if default_opt is None:
                default_opt = ET.SubElement(sibling, "option", name="isDefault")
            default_opt.set("value", "false")

    groups_option.append(new_group)
    write_xml(tree, workspace_file, dry_run)
    return {"action": "replaced" if replaced else "created", "file": workspace_file}


def build_legacy_bookmarks_xml(
    slots: list[BookmarkSlot],
    project_dir: str,
) -> ET.Element:
    """Build an old-format ``BookmarkManager`` component (pre-2022.1).

    IntelliJ's migration code reads this format from workspace.xml and
    converts it to the new ``BookmarksManager`` format on first load.
    The mnemonic is stored as a single character (``"1"``..``"0"``).
    """
    component = ET.Element("component", name="BookmarkManager")
    for slot in slots:
        rel = os.path.relpath(slot.path, project_dir).replace("\\", "/")
        url = f"file://$PROJECT_DIR$/{rel}"
        digit = slot.mnemonic.replace("DIGIT_", "")
        ET.SubElement(
            component,
            "bookmark",
            url=url,
            line="0",
            description=slot.label,
            mnemonic=digit,
        )
    return component


def inject_bookmarks(
    workspace_file: str,
    component: ET.Element,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Write a BookmarksManager or BookmarkManager component into a workspace file.

    Returns a dict describing what happened: ``{"action": "created"|"replaced", ...}``.
    """
    from augint_tools.ide.xml import read_xml, write_xml

    tree, root = read_xml(workspace_file)
    if tree is None or root is None:
        return {"action": "error", "reason": f"Could not read {workspace_file}"}

    comp_name = component.get("name", "")
    existing = root.find(f'.//component[@name="{comp_name}"]')
    replaced = existing is not None
    if existing is not None:
        root.remove(existing)

    root.append(component)
    write_xml(tree, workspace_file, dry_run)
    return {"action": "replaced" if replaced else "created", "file": workspace_file}


def bookmarks_already_set(
    workspace_file: str,
    expected_slots: list[BookmarkSlot],
    project_dir: str,
    group_name: str | None = None,
) -> bool:
    """Return True if the workspace file already has bookmarks matching ``expected_slots``."""
    from augint_tools.ide.xml import read_xml

    tree, root = read_xml(workspace_file)
    if root is None:
        return False

    mgr = root.find('.//component[@name="BookmarksManager"]')
    if mgr is None:
        return False

    group_states = mgr.findall('./option[@name="groups"]/GroupState')
    if group_name is not None:
        target_group = None
        for group in group_states:
            name_opt = group.find('./option[@name="name"]')
            if name_opt is not None and name_opt.get("value") == group_name:
                target_group = group
                break
        if target_group is None:
            return False
        groups_to_check = [target_group]
    else:
        groups_to_check = group_states or [mgr]

    # Collect existing mnemonic -> url mappings
    existing: dict[str, str] = {}
    for group in groups_to_check:
        for bstate in group.findall('.//option[@name="bookmarks"]/BookmarkState'):
            mtype = ""
            url = ""
            for opt in bstate.findall("option"):
                if opt.get("name") == "type":
                    mtype = opt.get("value", "")
            for entry in bstate.findall("attributes/entry"):
                if entry.get("key") == "url":
                    url = entry.get("value", "")
            if mtype and url:
                existing[mtype] = url

    for slot in expected_slots:
        rel = os.path.relpath(slot.path, project_dir).replace("\\", "/")
        expected_url = f"file://$PROJECT_DIR$/{rel}"
        if existing.get(slot.mnemonic) != expected_url:
            return False

    return True


def format_bookmark_table(slots: list[BookmarkSlot]) -> list[str]:
    """Return human-readable lines like ``  [1] Project config  -> pyproject.toml``."""
    lines: list[str] = []
    for slot in slots:
        digit = slot.mnemonic.replace("DIGIT_", "")
        lines.append(f"  [{digit}] {slot.label:<16s} -> {slot.rel}")
    return lines
