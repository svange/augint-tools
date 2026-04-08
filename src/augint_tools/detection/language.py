"""Language detection from filesystem markers."""

from pathlib import Path

PYTHON_MARKERS = ["pyproject.toml", "setup.py", "setup.cfg", "Pipfile", "requirements.txt"]
TYPESCRIPT_MARKERS = ["tsconfig.json"]
JAVASCRIPT_MARKERS = ["package.json"]


def detect_language(path: Path) -> str:
    """Detect the primary language of a repository.

    Returns: python, typescript, mixed, or unknown.
    """
    has_python = any((path / m).exists() for m in PYTHON_MARKERS)
    has_ts = any((path / m).exists() for m in TYPESCRIPT_MARKERS)
    has_js = any((path / m).exists() for m in JAVASCRIPT_MARKERS)

    if has_python and (has_ts or has_js):
        return "mixed"
    if has_python:
        return "python"
    if has_ts:
        return "typescript"
    if has_js:
        return "typescript"  # JS projects treated as TS ecosystem
    return "unknown"
