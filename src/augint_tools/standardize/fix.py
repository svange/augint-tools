"""Fix engine: apply template-backed or rule-backed fixes from audit findings."""

from pathlib import Path

from augint_tools.standardize.models import AuditResult, Finding, FixAction, FixResult


def apply_fixes(
    path: Path,
    audit_result: AuditResult,
    *,
    dry_run: bool = True,
    sections: list[str] | None = None,
) -> FixResult:
    """Apply fixes for fixable findings.

    Args:
        path: Repository root.
        audit_result: Previous audit result.
        dry_run: If True, plan but don't write. If False, apply changes.
        sections: Limit to specific sections.

    Returns:
        FixResult with planned and applied actions.
    """
    result = FixResult()

    for finding in audit_result.findings:
        if not finding.can_fix:
            continue
        if sections and finding.section not in sections:
            continue

        action = FixAction(
            finding_id=finding.id,
            fix_kind=finding.fix_kind,
            target=finding.subject,
            description=f"Fix {finding.id}: {finding.expected}",
        )
        result.actions_planned.append(action)

        if not dry_run:
            success = _apply_single_fix(path, finding, action)
            if success:
                result.actions_applied.append(action)
                if action.target not in result.files_changed:
                    result.files_changed.append(action.target)
            else:
                result.actions_failed.append(action)

    return result


def _apply_single_fix(path: Path, finding: Finding, action: FixAction) -> bool:
    """Apply a single fix. Returns True on success."""
    try:
        if finding.fix_kind == "generate":
            return _generate_file(path, finding)
        elif finding.fix_kind == "patch":
            return _patch_file(path, finding)
        else:
            # replace, external, manual: not auto-fixable in this version
            return False
    except Exception:
        return False


def _generate_file(path: Path, finding: Finding) -> bool:
    """Generate a missing file from defaults."""
    if finding.id == "dotfiles.editorconfig.missing":
        target = path / ".editorconfig"
        target.write_text(
            "root = true\n\n"
            "[*]\n"
            "indent_style = space\n"
            "indent_size = 4\n"
            "end_of_line = lf\n"
            "charset = utf-8\n"
            "trim_trailing_whitespace = true\n"
            "insert_final_newline = true\n"
        )
        return True

    if finding.id == "dotfiles.gitignore.missing":
        target = path / ".gitignore"
        target.write_text(
            "# Environment\n"
            ".env\n"
            ".env.*\n"
            "!.env.example\n\n"
            "# Python\n"
            "__pycache__/\n"
            "*.pyc\n"
            ".venv/\n"
            "dist/\n"
            "*.egg-info/\n\n"
            "# IDE\n"
            ".idea/\n"
            ".vscode/\n"
            "*.swp\n"
        )
        return True

    return False


def _patch_file(path: Path, finding: Finding) -> bool:
    """Patch an existing file."""
    if finding.id == "dotfiles.gitignore.env":
        gitignore = path / ".gitignore"
        content = gitignore.read_text() if gitignore.exists() else ""
        if ".env" not in content:
            content = content.rstrip() + "\n\n# Environment\n.env\n.env.*\n!.env.example\n"
            gitignore.write_text(content)
            return True

    return False
