"""XML read/write helpers matching IntelliJ's formatting conventions."""

from __future__ import annotations

import io
import os
import tempfile
import xml.etree.ElementTree as ET


def minimal_project_xml() -> tuple[ET.ElementTree[ET.Element[str]], ET.Element[str]]:
    """Return a new IntelliJ project XML tree plus its root element."""
    root = ET.Element("project", version="4")
    return ET.ElementTree(root), root


def minimal_application_xml() -> tuple[ET.ElementTree[ET.Element[str]], ET.Element[str]]:
    """Return a new JetBrains application XML tree plus its root element."""
    root = ET.Element("application")
    return ET.ElementTree(root), root


def read_xml(
    path: str,
) -> tuple[ET.ElementTree[ET.Element[str]] | None, ET.Element[str] | None]:
    """Parse an XML file; return (None, None) if missing or unparseable."""
    if not os.path.exists(path):
        return None, None
    try:
        tree = ET.parse(path)
        root = tree.getroot()
        if root is None:
            return None, None
        return tree, root
    except ET.ParseError:
        return None, None


def write_xml(tree: ET.ElementTree[ET.Element[str]], path: str, dry_run: bool = False) -> None:
    """Atomically write an XML tree, matching IntelliJ's declaration style.

    No-op when ``dry_run`` is True. IntelliJ expects double-quoted attributes in
    the XML declaration; ``ET.write`` emits single quotes, so we rewrite that
    line before committing.
    """
    ET.indent(tree, space="  ")
    buf = io.BytesIO()
    tree.write(buf, encoding="UTF-8", xml_declaration=True)
    content = buf.getvalue().decode("utf-8")
    content = content.replace(
        "<?xml version='1.0' encoding='UTF-8'?>",
        '<?xml version="1.0" encoding="UTF-8"?>',
    )
    if dry_run:
        return
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def find_component(root: ET.Element, name: str) -> ET.Element | None:
    return root.find(f'.//component[@name="{name}"]')


def get_or_create_component(root: ET.Element, name: str) -> ET.Element:
    el = find_component(root, name)
    if el is None:
        el = ET.SubElement(root, "component", name=name)
    return el
