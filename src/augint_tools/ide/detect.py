"""Detection helpers: read .env, git remote, venv metadata, and IDE paths."""

from __future__ import annotations

import configparser
import glob
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET  # nosemgrep: python.lang.security.use-defused-xml.use-defused-xml

import defusedxml.ElementTree as defused_ET

from augint_tools.ide.xml import find_component


def parse_dotenv(path: str) -> dict[str, str]:
    """Parse a .env file into a dict. Missing file returns an empty dict."""
    result: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                key, sep, val = line.partition("=")
                if not sep:
                    continue
                val = val.strip().strip('"').strip("'")
                result[key.strip()] = val
    except FileNotFoundError:
        pass
    return result


def upsert_dotenv(path: str, key: str, value: str) -> None:
    """Set ``key=value`` in a .env file, creating the file if absent.

    Preserves other lines and updates in place when the key already exists.
    """
    lines: list[str] = []
    found = False
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{key}={value}\n"
            found = True
            break
    if not found:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] = lines[-1] + "\n"
        lines.append(f"{key}={value}\n")
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def detect_python_version(venv_path: str) -> tuple[str, str]:
    """Return ``(full_version, major_minor)`` from pyvenv.cfg.

    Falls back to ``("3.12", "3.12")`` if pyvenv.cfg is missing or malformed.
    """
    cfg_path = os.path.join(venv_path, "pyvenv.cfg")
    try:
        with open(cfg_path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("version_info") or line.startswith("version"):
                    full = line.partition("=")[2].strip()
                    if not full:
                        continue
                    parts = full.split(".")
                    major_minor = ".".join(parts[:2]) if len(parts) >= 2 else full
                    return full, major_minor
    except FileNotFoundError:
        pass
    return "3.12", "3.12"


def detect_project_name(project_dir: str) -> str:
    """Return pyproject [project].name if present, else the dir basename."""
    pyproject = os.path.join(project_dir, "pyproject.toml")
    try:
        with open(pyproject, encoding="utf-8") as f:
            for line in f:
                m = re.match(r'^\s*name\s*=\s*["\']([^"\']+)["\']', line)
                if m:
                    return m.group(1)
    except FileNotFoundError:
        pass
    return os.path.basename(os.path.abspath(project_dir))


def find_iml_file(project_dir: str) -> str | None:
    """Find the project's .iml module file.

    Search order:
    1. ``modules.xml`` — the canonical source; reads the registered module filepath.
    2. ``.idea/*.iml`` — common when IDEA stores the module inside ``.idea/``.
    3. ``<project_root>/*.iml`` — legacy layout with the ``.iml`` at the project root.
    """
    # 1. modules.xml
    modules_xml = os.path.join(project_dir, ".idea", "modules.xml")
    if os.path.exists(modules_xml):
        try:
            tree = defused_ET.parse(modules_xml)
            for mod in tree.findall(".//module"):
                filepath = mod.get("filepath", "")
                resolved = filepath.replace("$PROJECT_DIR$", project_dir)
                if resolved.endswith(".iml") and os.path.exists(resolved):
                    return resolved
        except ET.ParseError:
            pass

    # 2. .idea/*.iml
    idea_matches = glob.glob(os.path.join(project_dir, ".idea", "*.iml"))
    if idea_matches:
        return idea_matches[0]

    # 3. root *.iml
    root_matches = glob.glob(os.path.join(project_dir, "*.iml"))
    return root_matches[0] if root_matches else None


def extract_project_id(workspace_xml_path: str) -> str | None:
    """Read the IntelliJ ProjectId from ``.idea/workspace.xml``.

    Returns the KSUID string (e.g. ``3CMMpoDrUzSqpNU3YNAXkZ3w9gH``) used to
    name the product workspace file, or ``None`` if workspace.xml is absent or
    doesn't contain a ProjectId component.
    """
    if not os.path.exists(workspace_xml_path):
        return None
    try:
        tree = defused_ET.parse(workspace_xml_path)
        root = tree.getroot()
    except ET.ParseError:
        return None
    if root is None:
        return None
    comp = find_component(root, "ProjectId")
    if comp is not None:
        pid = comp.get("id")
        if pid:
            return pid
    return None


def resolve_product_workspace(
    jb_options_dir: str | None,
    workspace_xml_path: str,
) -> str | None:
    """Locate the IntelliJ product workspace file for this project.

    Reads the ``ProjectId`` from ``.idea/workspace.xml`` and looks for
    ``{config_root}/workspace/{project_id}.xml``.  Returns the path if it
    exists, else ``None``.
    """
    if jb_options_dir is None:
        return None
    project_id = extract_project_id(workspace_xml_path)
    if project_id is None:
        return None
    config_root = os.path.dirname(jb_options_dir)
    candidate = os.path.join(config_root, "workspace", f"{project_id}.xml")
    if os.path.exists(candidate):
        return candidate
    return None


def parse_git_remote(project_dir: str) -> tuple[str, str, str] | None:
    """Return ``(owner, repo, canonical_url)`` for origin, or None if absent."""
    cfg_path = os.path.join(project_dir, ".git", "config")
    if not os.path.exists(cfg_path):
        return None
    parser = configparser.ConfigParser()
    try:
        parser.read(cfg_path, encoding="utf-8")
    except configparser.Error:
        return None
    for section in parser.sections():
        if section.lower() == 'remote "origin"':
            url = parser[section].get("url", "")
            m = re.search(r"github\.com[:/]([^/]+)/([^/\n]+?)(?:\.git)?$", url)
            if m:
                owner, repo = m.group(1), m.group(2)
                return owner, repo, f"https://github.com/{owner}/{repo}"
    return None


def extract_windows_project_path(root: ET.Element) -> str | None:
    """Read the Windows project path from CopilotPersistence in workspace.xml."""
    comp = find_component(root, "CopilotPersistence")
    if comp is None:
        return None
    for entry in comp.findall("persistenceIdMap/entry"):
        key = entry.get("key", "")
        stripped = key.lstrip("_")
        if re.match(r"[A-Za-z]:/", stripped):
            return stripped
    return None


def wslpath_to_windows(linux_path: str) -> str | None:
    """Convert a Linux path to Windows format via ``wslpath`` (WSL2 only)."""
    try:
        result = subprocess.run(
            ["wslpath", "-w", linux_path],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip().replace("\\", "/")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def find_jb_options_dir() -> str | None:
    """Find the newest IntelliJ IDEA user config options directory."""
    system = sys.platform
    if system == "win32":
        base = os.environ.get("APPDATA", "")
        pattern = os.path.join(base, "JetBrains", "IntelliJIdea*")
    elif system == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
        pattern = os.path.join(base, "JetBrains", "IntelliJIdea*")
    else:
        native = os.path.expanduser("~/.config/JetBrains")
        pattern = os.path.join(native, "IntelliJIdea*")
        if not glob.glob(pattern):
            pattern = "/mnt/c/Users/*/AppData/Roaming/JetBrains/IntelliJIdea*"

    def _ver_key(p: str) -> tuple[int, int]:
        m = re.search(r"(\d{4})\.(\d+)", os.path.basename(p))
        return (int(m.group(1)), int(m.group(2))) if m else (0, 0)

    dirs = sorted(
        [d for d in glob.glob(pattern) if os.path.isdir(d)],
        key=_ver_key,
        reverse=True,
    )
    if not dirs:
        return None
    options = os.path.join(dirs[0], "options")
    return options if os.path.isdir(options) else dirs[0]


def resolve_windows_paths(
    project_dir: str,
    venv_path: str,
    workspace_xml_path: str,
    override: str | None,
) -> tuple[str | None, str | None, str | None]:
    """Resolve ``(win_project_dir, win_venv, win_python)``.

    Uses, in order: the ``override`` argument, the CopilotPersistence entry in
    workspace.xml (if present and parseable), then ``wslpath``. Selects
    ``Scripts/python.exe`` or ``bin/python3`` based on what's present in the
    local venv.
    """
    win_project_dir = override
    if win_project_dir is None:
        _tree, root = (None, None)
        if os.path.exists(workspace_xml_path):
            try:
                tree = defused_ET.parse(workspace_xml_path)
                root = tree.getroot()
            except ET.ParseError:
                root = None
        if root is not None:
            win_project_dir = extract_windows_project_path(root)
    if win_project_dir is None:
        win_project_dir = wslpath_to_windows(project_dir)
    if win_project_dir is None:
        return None, None, None

    win_project_dir = win_project_dir.rstrip("/")
    win_venv = f"{win_project_dir}/.venv"
    if os.path.exists(os.path.join(venv_path, "Scripts", "python.exe")):
        win_python = f"{win_venv}/Scripts/python.exe"
    else:
        win_python = f"{win_venv}/bin/python3"
    return win_project_dir, win_venv, win_python
