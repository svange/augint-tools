"""Deployment-link store for the dashboard.

Manages ``~/.augint/deployments.yaml`` -- a per-user file that maps
``owner/repo`` slugs to a flat list of ``{label, url}`` entries. The dashboard
reads this file to surface deployment URLs on each repo card and in the
detail drawer.

Reserved labels ``dev``, ``main``, ``pypi`` get special rendering on the
card; any other label is treated as a free-form tag with its first letter
as the card glyph.

For Python library repos (``py`` tag, non-service, non-org) a ``pypi`` entry
is synthesised automatically from the repo name when the yaml doesn't
already declare one. Users can override the guess by adding a manual
``pypi`` entry -- manual always wins.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import yaml
from loguru import logger

from augint_tools.config import get_augint_home

if TYPE_CHECKING:
    from ._data import RepoStatus


DeploymentSource = Literal["yaml", "auto"]


@dataclass(frozen=True)
class DeploymentLink:
    """One deployment entry for a repo."""

    label: str
    url: str
    source: DeploymentSource = "yaml"


def get_deployments_path() -> Path:
    """Path to ``~/.augint/deployments.yaml`` (resolves to %USERPROFILE% on Windows)."""
    return get_augint_home() / "deployments.yaml"


def _load_raw(path: Path | None = None) -> dict[str, list[dict]]:
    """Load the raw yaml as ``{full_name: [{label, url}, ...]}``. Empty dict on failure."""
    p = path or get_deployments_path()
    if not p.exists():
        return {}
    try:
        with p.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (yaml.YAMLError, OSError) as exc:
        logger.warning(f"could not read {p}: {exc}")
        return {}
    if not isinstance(data, dict):
        logger.warning(f"{p} top level is not a mapping; ignoring")
        return {}
    clean: dict[str, list[dict]] = {}
    for repo, entries in data.items():
        if not isinstance(repo, str) or not isinstance(entries, list):
            continue
        rows: list[dict] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            label = entry.get("label")
            url = entry.get("url")
            if isinstance(label, str) and isinstance(url, str) and label and url:
                rows.append({"label": label, "url": url})
        if rows:
            clean[repo] = rows
    return clean


def load_deployments(path: Path | None = None) -> dict[str, list[DeploymentLink]]:
    """Return the yaml-declared links, one list per repo slug."""
    raw = _load_raw(path)
    return {
        repo: [DeploymentLink(label=row["label"], url=row["url"], source="yaml") for row in rows]
        for repo, rows in raw.items()
    }


def _save_raw(data: dict[str, list[dict]], path: Path | None = None) -> None:
    """Atomically write the raw deployments yaml.

    Uses a temp file in the same directory + ``os.replace`` so partial writes
    can never be observed. ``os.replace`` is cross-platform (unlike
    ``os.rename`` on Windows when the target exists).
    """
    p = path or get_deployments_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".deployments.", suffix=".yaml.tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, default_flow_style=False, sort_keys=True)
        os.replace(tmp_name, p)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def save_deployments(links: dict[str, list[DeploymentLink]], path: Path | None = None) -> None:
    """Persist the manual (``yaml``-sourced) links. Auto-derived entries are never written."""
    raw: dict[str, list[dict]] = {}
    for repo, entries in links.items():
        rows = [{"label": e.label, "url": e.url} for e in entries if e.source == "yaml"]
        if rows:
            raw[repo] = rows
    _save_raw(raw, path)


def add_link(
    full_name: str,
    label: str,
    url: str,
    *,
    path: Path | None = None,
) -> None:
    """Append a new ``{label, url}`` entry for ``full_name`` and save."""
    raw = _load_raw(path)
    raw.setdefault(full_name, []).append({"label": label, "url": url})
    _save_raw(raw, path)


def remove_link(
    full_name: str,
    label: str,
    url: str,
    *,
    path: Path | None = None,
) -> None:
    """Remove the first entry matching ``(label, url)`` for ``full_name`` and save."""
    raw = _load_raw(path)
    entries = raw.get(full_name)
    if not entries:
        return
    for idx, row in enumerate(entries):
        if row.get("label") == label and row.get("url") == url:
            entries.pop(idx)
            break
    if entries:
        raw[full_name] = entries
    else:
        raw.pop(full_name, None)
    _save_raw(raw, path)


def update_link(
    full_name: str,
    old_label: str,
    old_url: str,
    new_label: str,
    new_url: str,
    *,
    path: Path | None = None,
) -> None:
    """Replace the first ``(old_label, old_url)`` entry for ``full_name`` with new values."""
    raw = _load_raw(path)
    entries = raw.get(full_name)
    if not entries:
        return
    for idx, row in enumerate(entries):
        if row.get("label") == old_label and row.get("url") == old_url:
            entries[idx] = {"label": new_label, "url": new_url}
            break
    raw[full_name] = entries
    _save_raw(raw, path)


_IAC_FRAMEWORK_TAGS = frozenset({"cdk", "tf", "next", "vite"})


def pypi_package_name(status: RepoStatus) -> str | None:
    """Return a guessed PyPI package name for a Python library repo, else None.

    A repo qualifies only when ALL of the following hold:

    - Has the ``py`` tag (Python code present).
    - Not a service (``looks_like_service``).
    - Not an org repo (``is_org``).
    - No IaC/framework tags (``sam``, ``cdk``, ``tf``, ``next``, ``vite``) --
      these repos use ``pyproject.toml`` for dev-tooling, not packaging.
    - No dev branch -- Python libraries ship from main only; a dev branch
      signals a service/deploy workflow.

    The guess is the bare repo name. Users can override by adding a manual
    ``pypi`` entry in the yaml.
    """
    if "py" not in status.tags:
        return None
    if status.looks_like_service or status.is_org:
        return None
    if _IAC_FRAMEWORK_TAGS & set(status.tags):
        return None
    if status.has_dev_branch:
        return None
    return status.name


def resolve_links(
    status: RepoStatus,
    path: Path | None = None,
) -> list[DeploymentLink]:
    """Return all links for ``status``, merging yaml + auto-PyPI.

    Manual entries always win over the auto-PyPI synthesis (including for the
    ``pypi`` label).
    """
    by_repo = load_deployments(path)
    manual = list(by_repo.get(status.full_name, []))
    if any(link.label == "pypi" for link in manual):
        return manual
    pkg = pypi_package_name(status)
    if pkg is None:
        return manual
    auto = DeploymentLink(label="pypi", url=f"https://pypi.org/project/{pkg}/", source="auto")
    return manual + [auto]


def find_link(links: list[DeploymentLink], label: str) -> DeploymentLink | None:
    """Return the first link matching ``label`` or ``None``."""
    for link in links:
        if link.label == label:
            return link
    return None


_RESERVED_GLYPHS: dict[str, str] = {
    "dev": "s",
    "main": "p",
    "pypi": "π",
}


def tag_glyph(label: str) -> str:
    """Return the single-character glyph used on the card for ``label``.

    Reserved labels map to fixed glyphs (``dev``->s, ``main``->p, ``pypi``->π).
    Any other label takes its first lowercase character, or ``?`` if empty.
    """
    if label in _RESERVED_GLYPHS:
        return _RESERVED_GLYPHS[label]
    for ch in label.lower():
        if ch.isalnum():
            return ch
    return "?"


def sort_links_for_display(links: list[DeploymentLink]) -> list[DeploymentLink]:
    """Return links in display order: main, dev, pypi, then others in original order."""
    order = {"main": 0, "dev": 1, "pypi": 2}
    indexed = list(enumerate(links))
    indexed.sort(key=lambda item: (order.get(item[1].label, 99), item[0]))
    return [link for _, link in indexed]
