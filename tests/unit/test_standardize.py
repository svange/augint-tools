"""Tests for standardize engine."""

from augint_tools.detection.commands import CommandPlan
from augint_tools.detection.engine import GitHubState, RepoContext
from augint_tools.detection.toolchain import ToolchainInfo
from augint_tools.standardize.audit import run_audit
from augint_tools.standardize.fix import apply_fixes
from augint_tools.standardize.models import Finding


def _make_context(path, language="python", repo_kind="library"):
    return RepoContext(
        repo_kind=repo_kind,
        language=language,
        framework="plain",
        default_branch="main",
        dev_branch=None,
        current_branch="main",
        target_pr_branch="main",
        branch_strategy="main",
        toolchain=ToolchainInfo(package_manager="uv"),
        command_plan=CommandPlan(),
        github=GitHubState(),
        config_source="default",
        path=path,
    )


class TestAudit:
    def test_audit_empty_repo(self, tmp_path):
        """Audit a minimal repo detects missing files."""
        # Create minimal git-like structure
        context = _make_context(tmp_path)
        result = run_audit(tmp_path, context, "python-library")

        assert result.profile_id == "python-library"
        assert result.summary.sections_checked == 6
        assert len(result.findings) > 0

        # Should find missing .editorconfig
        ids = [f.id for f in result.findings]
        assert "dotfiles.editorconfig.missing" in ids
        assert "dotfiles.gitignore.missing" in ids

    def test_audit_with_section_filter(self, tmp_path):
        context = _make_context(tmp_path)
        result = run_audit(tmp_path, context, "python-library", sections=["dotfiles"])

        assert result.summary.sections_checked == 1
        for f in result.findings:
            assert f.section == "dotfiles"

    def test_audit_clean_dotfiles(self, tmp_path):
        """Repo with .editorconfig and .gitignore with .env should pass dotfiles."""
        (tmp_path / ".editorconfig").write_text("root = true\n")
        (tmp_path / ".gitignore").write_text(".env\n")

        context = _make_context(tmp_path)
        result = run_audit(tmp_path, context, "python-library", sections=["dotfiles"])

        dotfile_findings = [f for f in result.findings if f.section == "dotfiles"]
        assert len(dotfile_findings) == 0


class TestFix:
    def test_dry_run(self, tmp_path):
        context = _make_context(tmp_path)
        audit_result = run_audit(tmp_path, context, "python-library", sections=["dotfiles"])

        fix_result = apply_fixes(tmp_path, audit_result, dry_run=True)
        assert len(fix_result.actions_planned) > 0
        assert len(fix_result.actions_applied) == 0
        assert not (tmp_path / ".editorconfig").exists()

    def test_write_editorconfig(self, tmp_path):
        context = _make_context(tmp_path)
        audit_result = run_audit(tmp_path, context, "python-library", sections=["dotfiles"])

        fix_result = apply_fixes(tmp_path, audit_result, dry_run=False, sections=["dotfiles"])
        assert len(fix_result.actions_applied) > 0
        assert (tmp_path / ".editorconfig").exists()

    def test_write_gitignore(self, tmp_path):
        context = _make_context(tmp_path)
        audit_result = run_audit(tmp_path, context, "python-library", sections=["dotfiles"])

        apply_fixes(tmp_path, audit_result, dry_run=False, sections=["dotfiles"])
        assert (tmp_path / ".gitignore").exists()
        content = (tmp_path / ".gitignore").read_text()
        assert ".env" in content

    def test_patch_gitignore_env(self, tmp_path):
        """Patch .gitignore to add .env when missing."""
        (tmp_path / ".editorconfig").write_text("root = true\n")
        (tmp_path / ".gitignore").write_text("*.pyc\n")

        context = _make_context(tmp_path)
        audit_result = run_audit(tmp_path, context, "python-library", sections=["dotfiles"])

        apply_fixes(tmp_path, audit_result, dry_run=False, sections=["dotfiles"])
        content = (tmp_path / ".gitignore").read_text()
        assert ".env" in content


class TestFinding:
    def test_to_dict(self):
        f = Finding(
            id="test.finding",
            section="test",
            severity="error",
            subject="test.txt",
            actual="missing",
            expected="present",
            can_fix=True,
            fix_kind="generate",
            source="test rule",
        )
        d = f.to_dict()
        assert d["id"] == "test.finding"
        assert d["severity"] == "error"
        assert d["can_fix"] is True
