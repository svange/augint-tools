"""Tests for IDE setup helpers, steps, and CLI."""

from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from click.testing import CliRunner

from augint_tools.cli.__main__ import cli
from augint_tools.cli.commands.ide import ide
from augint_tools.ide import (
    step_bookmarks,
    step_github_tasks,
    step_jdk_table,
    step_module_sdk,
    step_project_sdk,
    step_project_structure,
    step_terminal_right,
)
from augint_tools.ide.detect import (
    bootstrap_github_env,
    detect_project_name,
    detect_python_version,
    ensure_iml_file,
    ensure_project_root_manager,
    external_storage_enabled,
    extract_windows_project_path,
    find_iml_file,
    parse_dotenv,
    parse_git_remote,
    upsert_dotenv,
)
from augint_tools.ide.xml import (
    find_component,
    get_or_create_component,
    minimal_project_xml,
    read_xml,
    write_xml,
)

# ---------------------------------------------------------------------------
# detect.py
# ---------------------------------------------------------------------------


class TestDetect:
    def test_parse_dotenv_reads_simple_kv(self, tmp_path: Path) -> None:
        p = tmp_path / ".env"
        p.write_text("FOO=bar\nBAZ=qux\n")
        assert parse_dotenv(str(p)) == {"FOO": "bar", "BAZ": "qux"}

    def test_parse_dotenv_strips_quotes_and_comments(self, tmp_path: Path) -> None:
        p = tmp_path / ".env"
        p.write_text("# comment\nA=\"hello\"\nB='world'\n\n")
        assert parse_dotenv(str(p)) == {"A": "hello", "B": "world"}

    def test_parse_dotenv_missing_returns_empty(self, tmp_path: Path) -> None:
        assert parse_dotenv(str(tmp_path / "nope.env")) == {}

    def test_upsert_dotenv_creates_file(self, tmp_path: Path) -> None:
        p = tmp_path / ".env"
        upsert_dotenv(str(p), "GH_TOKEN", "ghp_x")
        assert p.read_text() == "GH_TOKEN=ghp_x\n"

    def test_upsert_dotenv_updates_existing_key(self, tmp_path: Path) -> None:
        p = tmp_path / ".env"
        p.write_text("FOO=1\nGH_TOKEN=old\nBAR=2\n")
        upsert_dotenv(str(p), "GH_TOKEN", "new")
        assert p.read_text() == "FOO=1\nGH_TOKEN=new\nBAR=2\n"

    def test_upsert_dotenv_appends_when_missing(self, tmp_path: Path) -> None:
        p = tmp_path / ".env"
        p.write_text("FOO=1\n")
        upsert_dotenv(str(p), "GH_TOKEN", "ghp_x")
        assert p.read_text() == "FOO=1\nGH_TOKEN=ghp_x\n"

    def test_upsert_dotenv_handles_missing_trailing_newline(self, tmp_path: Path) -> None:
        p = tmp_path / ".env"
        p.write_text("FOO=1")
        upsert_dotenv(str(p), "GH_TOKEN", "x")
        assert p.read_text() == "FOO=1\nGH_TOKEN=x\n"

    def test_bootstrap_github_env_creates_blank_keys(self, tmp_path: Path) -> None:
        p = tmp_path / ".env"
        written = bootstrap_github_env(str(p))
        assert written == ["GH_ACCOUNT=", "GH_REPO="]
        assert p.read_text() == "GH_ACCOUNT=\nGH_REPO=\n"

    def test_bootstrap_github_env_preserves_existing_values(self, tmp_path: Path) -> None:
        p = tmp_path / ".env"
        p.write_text("GH_ACCOUNT=octo\n")
        written = bootstrap_github_env(str(p), owner="ignored", repo="hello")
        assert written == ["GH_REPO=hello"]
        assert p.read_text() == "GH_ACCOUNT=octo\nGH_REPO=hello\n"

    def test_detect_python_version_from_pyvenv(self, tmp_path: Path) -> None:
        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("version_info = 3.12.10.final.0\n")
        full, mm = detect_python_version(str(venv))
        assert full == "3.12.10.final.0"
        assert mm == "3.12"

    def test_detect_python_version_fallback(self, tmp_path: Path) -> None:
        full, mm = detect_python_version(str(tmp_path / "no-venv"))
        assert full == "3.12"
        assert mm == "3.12"

    def test_detect_project_name_from_pyproject(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "cool-proj"\n')
        assert detect_project_name(str(tmp_path)) == "cool-proj"

    def test_detect_project_name_falls_back_to_dirname(self, tmp_path: Path) -> None:
        d = tmp_path / "my-repo"
        d.mkdir()
        assert detect_project_name(str(d)) == "my-repo"

    def test_find_iml_file_none(self, tmp_path: Path) -> None:
        assert find_iml_file(str(tmp_path)) is None

    def test_find_iml_file_root(self, tmp_path: Path) -> None:
        (tmp_path / "proj.iml").write_text("<module/>")
        assert find_iml_file(str(tmp_path)) == str(tmp_path / "proj.iml")

    def test_find_iml_file_inside_idea(self, tmp_path: Path) -> None:
        idea = tmp_path / ".idea"
        idea.mkdir()
        (idea / "my-proj.iml").write_text("<module/>")
        assert find_iml_file(str(tmp_path)) == str(idea / "my-proj.iml")

    def test_find_iml_file_from_modules_xml(self, tmp_path: Path) -> None:
        idea = tmp_path / ".idea"
        idea.mkdir()
        (idea / "proj.iml").write_text("<module/>")
        (idea / "modules.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<project version="4">\n'
            '  <component name="ProjectModuleManager">\n'
            "    <modules>\n"
            '      <module fileurl="file://$PROJECT_DIR$/.idea/proj.iml"'
            ' filepath="$PROJECT_DIR$/.idea/proj.iml" />\n'
            "    </modules>\n"
            "  </component>\n"
            "</project>\n"
        )
        assert find_iml_file(str(tmp_path)) == str(idea / "proj.iml")

    def test_parse_git_remote_ssh(self, tmp_path: Path) -> None:
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text(
            '[remote "origin"]\n\turl = git@github.com:octo/hello.git\n'
        )
        assert parse_git_remote(str(tmp_path)) == (
            "octo",
            "hello",
            "https://github.com/octo/hello",
        )

    def test_parse_git_remote_https(self, tmp_path: Path) -> None:
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text(
            '[remote "origin"]\n\turl = https://github.com/octo/hello.git\n'
        )
        assert parse_git_remote(str(tmp_path)) == (
            "octo",
            "hello",
            "https://github.com/octo/hello",
        )

    def test_parse_git_remote_missing(self, tmp_path: Path) -> None:
        assert parse_git_remote(str(tmp_path)) is None

    def test_extract_windows_project_path(self) -> None:
        xml_str = """<project version="4">
          <component name="CopilotPersistence">
            <persistenceIdMap>
              <entry key="_C:/Users/me/projects/foo" value="x"/>
            </persistenceIdMap>
          </component>
        </project>"""
        root = ET.fromstring(xml_str)
        assert extract_windows_project_path(root) == "C:/Users/me/projects/foo"

    def test_extract_windows_project_path_absent(self) -> None:
        root = ET.fromstring("<project version='4'/>")
        assert extract_windows_project_path(root) is None

    # --- ensure_iml_file ---

    def test_ensure_iml_file_no_idea_dir(self, tmp_path: Path) -> None:
        assert ensure_iml_file(str(tmp_path), "proj") is None

    def test_ensure_iml_file_already_exists(self, tmp_path: Path) -> None:
        idea = tmp_path / ".idea"
        idea.mkdir()
        (idea / "proj.iml").write_text("<module/>")
        result = ensure_iml_file(str(tmp_path), "proj")
        assert result == str(idea / "proj.iml")
        # Should not create a duplicate
        assert len(list(idea.glob("*.iml"))) == 1

    def test_ensure_iml_file_creates_iml_and_modules_xml(self, tmp_path: Path) -> None:
        idea = tmp_path / ".idea"
        idea.mkdir()
        result = ensure_iml_file(str(tmp_path), "my-project")
        assert result is not None
        assert result == str(idea / "my-project.iml")
        assert os.path.exists(result)

        # Verify .iml content
        iml_content = Path(result).read_text()
        assert "PYTHON_MODULE" in iml_content
        assert "NewModuleRootManager" in iml_content

        # Verify modules.xml was created
        modules_xml = idea / "modules.xml"
        assert modules_xml.exists()
        content = modules_xml.read_text()
        assert "my-project.iml" in content

    def test_ensure_iml_file_updates_existing_modules_xml(self, tmp_path: Path) -> None:
        idea = tmp_path / ".idea"
        idea.mkdir()
        (idea / "modules.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<project version="4">\n'
            '  <component name="ProjectModuleManager">\n'
            "    <modules>\n"
            "    </modules>\n"
            "  </component>\n"
            "</project>\n"
        )
        result = ensure_iml_file(str(tmp_path), "proj")
        assert result is not None
        content = (idea / "modules.xml").read_text()
        assert "proj.iml" in content

    def test_ensure_iml_file_moves_root_iml_into_idea(self, tmp_path: Path) -> None:
        idea = tmp_path / ".idea"
        idea.mkdir()
        root_iml = tmp_path / "legacy.iml"
        root_iml.write_text("<module/>")

        result = ensure_iml_file(str(tmp_path), "proj")

        assert result == str(idea / "proj.iml")
        assert not root_iml.exists()
        assert (idea / "proj.iml").exists()
        modules_xml = (idea / "modules.xml").read_text()
        assert "$PROJECT_DIR$/.idea/proj.iml" in modules_xml

    def test_ensure_iml_file_rewrites_existing_modules_xml_from_root_to_idea(
        self, tmp_path: Path
    ) -> None:
        idea = tmp_path / ".idea"
        idea.mkdir()
        root_iml = tmp_path / "proj.iml"
        root_iml.write_text("<module/>")
        (idea / "modules.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<project version="4">\n'
            '  <component name="ProjectModuleManager">\n'
            "    <modules>\n"
            '      <module fileurl="file://$PROJECT_DIR$/proj.iml" filepath="$PROJECT_DIR$/proj.iml" />\n'
            "    </modules>\n"
            "  </component>\n"
            "</project>\n"
        )

        result = ensure_iml_file(str(tmp_path), "proj")

        assert result == str(idea / "proj.iml")
        assert not root_iml.exists()
        content = (idea / "modules.xml").read_text()
        assert "$PROJECT_DIR$/.idea/proj.iml" in content
        assert "$PROJECT_DIR$/proj.iml" not in content

    def test_ensure_iml_file_find_iml_file_roundtrip(self, tmp_path: Path) -> None:
        """After ensure_iml_file, find_iml_file should locate the new file."""
        idea = tmp_path / ".idea"
        idea.mkdir()
        created = ensure_iml_file(str(tmp_path), "test-proj")
        found = find_iml_file(str(tmp_path))
        assert created == found

    def test_external_storage_enabled(self, tmp_path: Path) -> None:
        misc = tmp_path / "misc.xml"
        misc.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<project version="4">\n'
            '  <component name="ExternalStorageConfigurationManager" enabled="true" />\n'
            "</project>\n"
        )
        assert external_storage_enabled(str(misc)) is True

    def test_ensure_project_root_manager_creates_misc(self, tmp_path: Path) -> None:
        misc = tmp_path / ".idea" / "misc.xml"
        changed = ensure_project_root_manager(str(misc))
        assert changed is True
        content = misc.read_text()
        assert "ProjectRootManager" in content

    def test_ensure_project_root_manager_adds_missing_component(self, tmp_path: Path) -> None:
        misc = tmp_path / ".idea" / "misc.xml"
        misc.parent.mkdir()
        misc.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<project version="4">\n'
            '  <component name="ExternalStorageConfigurationManager" enabled="true" />\n'
            "</project>\n"
        )
        changed = ensure_project_root_manager(str(misc))
        assert changed is True
        content = misc.read_text()
        assert "ExternalStorageConfigurationManager" in content
        assert "ProjectRootManager" in content


# ---------------------------------------------------------------------------
# xml.py
# ---------------------------------------------------------------------------


class TestXmlHelpers:
    def test_write_then_read_roundtrip(self, tmp_path: Path) -> None:
        path = str(tmp_path / "workspace.xml")
        tree, root = minimal_project_xml()
        get_or_create_component(root, "Foo")
        write_xml(tree, path)
        assert os.path.exists(path)
        content = Path(path).read_text()
        assert content.startswith('<?xml version="1.0" encoding="UTF-8"?>')
        assert '<component name="Foo"' in content

        tree2, root2 = read_xml(path)
        assert root2 is not None
        assert find_component(root2, "Foo") is not None

    def test_write_xml_dry_run_does_not_write(self, tmp_path: Path) -> None:
        path = str(tmp_path / "ws.xml")
        tree, _ = minimal_project_xml()
        write_xml(tree, path, dry_run=True)
        assert not os.path.exists(path)

    def test_read_xml_missing_returns_none(self, tmp_path: Path) -> None:
        tree, root = read_xml(str(tmp_path / "nope.xml"))
        assert tree is None and root is None

    def test_read_xml_malformed_returns_none(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.xml"
        p.write_text("<not>closed")
        tree, root = read_xml(str(p))
        assert tree is None and root is None


# ---------------------------------------------------------------------------
# Fixtures for step tests
# ---------------------------------------------------------------------------


@pytest.fixture
def idea_project(tmp_path: Path) -> Path:
    """A tmp project mirroring real IntelliJ layout: .iml inside .idea/."""
    idea = tmp_path / ".idea"
    idea.mkdir()
    (idea / "misc.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<project version="4">\n'
        '  <component name="ProjectRootManager" version="2" />\n'
        "</project>\n"
    )
    (idea / "modules.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<project version="4">\n'
        '  <component name="ProjectModuleManager">\n'
        "    <modules>\n"
        '      <module fileurl="file://$PROJECT_DIR$/.idea/proj.iml"'
        ' filepath="$PROJECT_DIR$/.idea/proj.iml" />\n'
        "    </modules>\n"
        "  </component>\n"
        "</project>\n"
    )
    (idea / "proj.iml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<module type="JAVA_MODULE" version="4">\n'
        '  <component name="NewModuleRootManager" inherit-compiler-output="true">\n'
        "    <exclude-output />\n"
        '    <content url="file://$MODULE_DIR$"/>\n'
        '    <orderEntry type="inheritedJdk" />\n'
        '    <orderEntry type="sourceFolder" forTests="false" />\n'
        "  </component>\n"
        "</module>\n"
    )
    return tmp_path


# ---------------------------------------------------------------------------
# steps.py
# ---------------------------------------------------------------------------


class TestSteps:
    def test_terminal_right_creates_workspace(self, idea_project: Path) -> None:
        ws = str(idea_project / ".idea" / "workspace.xml")
        res = step_terminal_right(ws)
        assert res.status == "ok"
        assert res.name == "terminal"
        content = Path(ws).read_text()
        assert 'id="Terminal"' in content
        assert 'anchor="right"' in content

    def test_terminal_right_idempotent(self, idea_project: Path) -> None:
        ws = str(idea_project / ".idea" / "workspace.xml")
        step_terminal_right(ws)
        res = step_terminal_right(ws)
        assert res.status == "skipped"

    def test_module_sdk_inherited_skips(self, idea_project: Path) -> None:
        """When the .iml uses inheritedJdk, module_sdk should skip."""
        iml = str(idea_project / ".idea" / "proj.iml")
        res = step_module_sdk(iml, "Python 3.12 (myproj)")
        assert res.status == "skipped"
        assert "inherits" in res.message.lower()

    def test_module_sdk_sets_explicit_jdk(self, tmp_path: Path) -> None:
        """When no jdk or inheritedJdk entry exists, adds one."""
        iml = tmp_path / "bare.iml"
        iml.write_text(
            '<module type="JAVA_MODULE" version="4">\n'
            '  <component name="NewModuleRootManager">\n'
            '    <content url="file://$MODULE_DIR$"/>\n'
            "  </component>\n"
            "</module>\n"
        )
        res = step_module_sdk(str(iml), "Python 3.12 (x)")
        assert res.status == "ok"
        _, root = read_xml(str(iml))
        assert root is not None
        jdk = root.find('.//orderEntry[@type="jdk"]')
        assert jdk is not None
        assert jdk.get("jdkName") == "Python 3.12 (x)"

    def test_module_sdk_idempotent_explicit(self, tmp_path: Path) -> None:
        iml = tmp_path / "bare.iml"
        iml.write_text(
            '<module type="JAVA_MODULE" version="4">\n'
            '  <component name="NewModuleRootManager">\n'
            '    <content url="file://$MODULE_DIR$"/>\n'
            "  </component>\n"
            "</module>\n"
        )
        step_module_sdk(str(iml), "Python 3.12 (x)")
        res = step_module_sdk(str(iml), "Python 3.12 (x)")
        assert res.status == "skipped"

    def test_module_sdk_no_iml_errors(self) -> None:
        res = step_module_sdk(None, "Python 3.12")
        assert res.status == "error"

    def test_project_sdk_sets_and_is_idempotent(self, idea_project: Path) -> None:
        misc = str(idea_project / ".idea" / "misc.xml")
        r1 = step_project_sdk(misc, "Python 3.12 (x)")
        assert r1.status == "ok"
        r2 = step_project_sdk(misc, "Python 3.12 (x)")
        assert r2.status == "skipped"

    def test_project_structure_adds_sources_tests_excludes(self, idea_project: Path) -> None:
        (idea_project / "src").mkdir()
        (idea_project / "tests").mkdir()
        iml = str(idea_project / ".idea" / "proj.iml")
        res = step_project_structure(iml, str(idea_project), "proj")
        assert res.status == "ok"
        content = Path(iml).read_text()
        assert 'url="file://$MODULE_DIR$/src"' in content
        assert 'url="file://$MODULE_DIR$/tests"' in content
        assert 'isTestSource="true"' in content
        assert 'url="file://$MODULE_DIR$/dist"' in content  # always-exclude

    def test_project_structure_idempotent(self, idea_project: Path) -> None:
        (idea_project / "src").mkdir()
        iml = str(idea_project / ".idea" / "proj.iml")
        step_project_structure(iml, str(idea_project), "proj")
        res = step_project_structure(iml, str(idea_project), "proj")
        assert res.status == "skipped"

    def test_github_tasks_no_remote_skipped(self, idea_project: Path) -> None:
        ws = str(idea_project / ".idea" / "workspace.xml")
        res = step_github_tasks(ws, str(idea_project), "ghp_x")
        assert res.status == "skipped"

    def test_github_tasks_writes_native_format(self, idea_project: Path) -> None:
        git = idea_project / ".git"
        git.mkdir()
        (git / "config").write_text('[remote "origin"]\n\turl = git@github.com:octo/hello.git\n')
        ws = str(idea_project / ".idea" / "workspace.xml")
        res = step_github_tasks(ws, str(idea_project), "ghp_secret")
        assert res.status == "ok"
        content = Path(ws).read_text()
        # Uses native <GitHub> tag, not <server>
        assert "<GitHub " in content
        assert 'url="https://github.com"' in content
        assert 'value="octo"' in content
        assert 'value="hello"' in content
        # Token IS stored in XML (IDEA reads it, then moves to OS keyring)
        assert "ghp_secret" in content

    def test_github_tasks_idempotent(self, idea_project: Path) -> None:
        git = idea_project / ".git"
        git.mkdir()
        (git / "config").write_text('[remote "origin"]\n\turl = git@github.com:octo/hello.git\n')
        ws = str(idea_project / ".idea" / "workspace.xml")
        step_github_tasks(ws, str(idea_project), "ghp_secret")
        res = step_github_tasks(ws, str(idea_project), "ghp_secret")
        assert res.status == "skipped"

    def test_jdk_table_no_options_dir_action_required(self) -> None:
        res = step_jdk_table(None, "Python 3.12", "3.12.0", None, None)
        assert res.status == "action-required"
        assert "jb_options_dir" in res.missing_inputs

    def test_jdk_table_no_windows_path_action_required(self, tmp_path: Path) -> None:
        res = step_jdk_table(str(tmp_path), "Python 3.12", "3.12.0", None, None)
        assert res.status == "action-required"
        assert "windows_project_dir" in res.missing_inputs

    def test_jdk_table_writes_entry(self, tmp_path: Path) -> None:
        opts = tmp_path / "options"
        opts.mkdir()
        res = step_jdk_table(
            str(opts),
            "Python 3.12 (x)",
            "3.12.0",
            "C:/Users/me/p/.venv/bin/python3",
            "C:/Users/me/p/.venv",
        )
        assert res.status == "ok"
        content = (opts / "jdk.table.xml").read_text()
        assert 'value="Python 3.12 (x)"' in content
        assert 'value="Python 3.12.0"' in content

    def test_jdk_table_idempotent(self, tmp_path: Path) -> None:
        opts = tmp_path / "options"
        opts.mkdir()
        step_jdk_table(str(opts), "Python 3.12 (x)", "3.12", "C:/py.exe", "C:/.venv")
        res = step_jdk_table(str(opts), "Python 3.12 (x)", "3.12", "C:/py.exe", "C:/.venv")
        assert res.status == "skipped"


# ---------------------------------------------------------------------------
# bookmarks.py
# ---------------------------------------------------------------------------


class TestBookmarks:
    def test_discover_bookmarks_python_project(self, tmp_path: Path) -> None:
        from augint_tools.ide.bookmarks import discover_bookmarks

        (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
        (tmp_path / ".env").write_text("SECRET=1\n")
        (tmp_path / "CLAUDE.md").write_text("# AI context\n")
        (tmp_path / "README.md").write_text("# Hello\n")

        slots = discover_bookmarks(str(tmp_path))
        mnemonics = {s.mnemonic for s in slots}
        assert "DIGIT_1" in mnemonics  # pyproject.toml
        assert "DIGIT_2" in mnemonics  # CLAUDE.md
        assert "DIGIT_4" in mnemonics  # .env
        assert "DIGIT_6" in mnemonics  # README.md

    def test_discover_bookmarks_empty_project(self, tmp_path: Path) -> None:
        from augint_tools.ide.bookmarks import discover_bookmarks

        assert discover_bookmarks(str(tmp_path)) == []

    def test_discover_bookmarks_entry_point(self, tmp_path: Path) -> None:
        from augint_tools.ide.bookmarks import discover_bookmarks

        src = tmp_path / "src" / "myapp" / "cli"
        src.mkdir(parents=True)
        (src / "__main__.py").write_text("def main(): pass\n")

        slots = discover_bookmarks(str(tmp_path))
        entry = next((s for s in slots if s.mnemonic == "DIGIT_3"), None)
        assert entry is not None
        assert "__main__.py" in entry.rel

    def test_build_and_inject_bookmarks(self, tmp_path: Path) -> None:
        from augint_tools.ide.bookmarks import (
            BookmarkSlot,
            inject_bookmark_group,
        )
        from augint_tools.ide.xml import minimal_project_xml, write_xml

        # Create a product workspace file
        ws_path = str(tmp_path / "workspace.xml")
        tree, root = minimal_project_xml()
        write_xml(tree, ws_path)

        slots = [
            BookmarkSlot(
                mnemonic="DIGIT_1",
                label="Config",
                path=str(tmp_path / "pyproject.toml"),
                rel="pyproject.toml",
            ),
        ]
        result = inject_bookmark_group(ws_path, slots, str(tmp_path), "test")
        assert result["action"] == "created"

        content = Path(ws_path).read_text()
        assert "BookmarksManager" in content
        assert 'value="test"' in content
        assert "DIGIT_1" in content
        assert "pyproject.toml" in content

    def test_bookmarks_already_set(self, tmp_path: Path) -> None:
        from augint_tools.ide.bookmarks import (
            BookmarkSlot,
            bookmarks_already_set,
            inject_bookmark_group,
        )
        from augint_tools.ide.xml import minimal_project_xml, write_xml

        ws_path = str(tmp_path / "workspace.xml")
        tree, root = minimal_project_xml()
        write_xml(tree, ws_path)

        (tmp_path / "pyproject.toml").write_text("")
        slots = [
            BookmarkSlot(
                mnemonic="DIGIT_1",
                label="Config",
                path=str(tmp_path / "pyproject.toml"),
                rel="pyproject.toml",
            ),
        ]
        inject_bookmark_group(ws_path, slots, str(tmp_path), "x")

        assert bookmarks_already_set(ws_path, slots, str(tmp_path), "x")

    def test_inject_bookmark_group_preserves_existing_groups(self, tmp_path: Path) -> None:
        import xml.etree.ElementTree as ET

        from augint_tools.ide.bookmarks import BookmarkSlot, inject_bookmark_group
        from augint_tools.ide.xml import minimal_project_xml, write_xml

        ws_path = str(tmp_path / "workspace.xml")
        tree, _root = minimal_project_xml()
        write_xml(tree, ws_path)

        existing = """
<component name="BookmarksManager">
  <option name="groups">
    <GroupState>
      <option name="bookmarks">
        <BookmarkState>
          <attributes>
            <entry key="url" value="file://$PROJECT_DIR$/.env.example" />
            <entry key="line" value="0" />
          </attributes>
          <option name="provider" value="com.intellij.ide.bookmark.providers.LineBookmarkProvider" />
          <option name="type" value="DEFAULT" />
        </BookmarkState>
      </option>
      <option name="isDefault" value="false" />
      <option name="name" value="NEW LIST" />
    </GroupState>
  </option>
</component>
"""
        tree, root = minimal_project_xml()
        root.append(ET.fromstring(existing))
        write_xml(tree, ws_path)

        slots = [
            BookmarkSlot(
                mnemonic="DIGIT_1",
                label="Config",
                path=str(tmp_path / "pyproject.toml"),
                rel="pyproject.toml",
            ),
        ]
        result = inject_bookmark_group(ws_path, slots, str(tmp_path), "augint-tools")
        assert result["action"] == "created"

        content = Path(ws_path).read_text()
        assert 'value="NEW LIST"' in content
        assert ".env.example" in content
        assert 'value="augint-tools"' in content
        assert 'value="true"' in content

    def test_step_bookmarks_no_workspace_file(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\n')
        ws_path = str(tmp_path / ".idea" / "workspace.xml")
        res = step_bookmarks(str(tmp_path), "x", ws_path)
        assert res.status == "action-required"
        assert "workspace.xml" in res.missing_inputs

    def test_step_bookmarks_no_files(self, tmp_path: Path) -> None:
        ws_path = str(tmp_path / ".idea" / "workspace.xml")
        res = step_bookmarks(str(tmp_path), "x", ws_path)
        assert res.status == "skipped"

    def test_step_bookmarks_writes_legacy_format(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\n')
        (tmp_path / "README.md").write_text("# x\n")
        idea = tmp_path / ".idea"
        idea.mkdir()
        ws_path = idea / "workspace.xml"
        ws_path.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<project version="4"></project>\n'
        )
        res = step_bookmarks(str(tmp_path), "x", str(ws_path))
        assert res.status == "ok"
        content = ws_path.read_text()
        assert '<component name="BookmarkManager">' in content
        assert 'mnemonic="1"' in content
        assert "pyproject.toml" in content

    def test_step_bookmarks_always_uses_workspace_xml_even_when_product_workspace_exists(
        self, tmp_path: Path
    ) -> None:
        """product_workspace_path is ignored; legacy format always goes to workspace.xml."""
        from augint_tools.ide.xml import minimal_project_xml, write_xml

        (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\n')
        idea = tmp_path / ".idea"
        idea.mkdir()
        ws_path = idea / "workspace.xml"
        ws_path.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<project version="4"></project>\n'
        )
        product_ws = tmp_path / "product-workspace.xml"
        tree, _root = minimal_project_xml()
        write_xml(tree, str(product_ws))
        original_product = product_ws.read_text()

        res = step_bookmarks(str(tmp_path), "x", str(ws_path), str(product_ws))
        assert res.status == "ok"
        # Bookmarks written to workspace.xml in legacy format
        ws_content = ws_path.read_text()
        assert '<component name="BookmarkManager">' in ws_content
        assert 'mnemonic="1"' in ws_content
        # Product workspace must NOT be modified
        assert product_ws.read_text() == original_product

    def test_step_bookmarks_idempotent(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\n')
        idea = tmp_path / ".idea"
        idea.mkdir()
        ws_path = idea / "workspace.xml"
        ws_path.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<project version="4"></project>\n'
        )

        step_bookmarks(str(tmp_path), "x", str(ws_path))
        res = step_bookmarks(str(tmp_path), "x", str(ws_path))
        assert res.status == "skipped"

    def test_find_product_workspace_file(self, tmp_path: Path) -> None:
        from augint_tools.ide.bookmarks import find_product_workspace_file

        config_root = tmp_path / "JetBrains" / "IntelliJIdea2026.1"
        options = config_root / "options"
        options.mkdir(parents=True)
        workspace = config_root / "workspace"
        workspace.mkdir()

        # Write a product workspace file referencing the project
        (workspace / "abc123.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            "<application>\n"
            '  <component name="SomeState">\n'
            '    <option value="C:/Users/me/projects/myproj" />\n'
            "  </component>\n"
            "</application>\n"
        )

        result = find_product_workspace_file(str(options), "C:/Users/me/projects/myproj")
        assert result is not None
        assert "abc123.xml" in result

    def test_find_product_workspace_file_not_found(self, tmp_path: Path) -> None:
        from augint_tools.ide.bookmarks import find_product_workspace_file

        assert find_product_workspace_file(None, None) is None
        assert find_product_workspace_file(str(tmp_path), "C:/nope") is None

    def test_format_bookmark_table(self) -> None:
        from augint_tools.ide.bookmarks import BookmarkSlot, format_bookmark_table

        slots = [
            BookmarkSlot("DIGIT_1", "Project config", "/a/pyproject.toml", "pyproject.toml"),
            BookmarkSlot("DIGIT_4", "Environment", "/a/.env", ".env"),
        ]
        lines = format_bookmark_table(slots)
        assert len(lines) == 2
        assert "[1]" in lines[0]
        assert "pyproject.toml" in lines[0]
        assert "[4]" in lines[1]


# ---------------------------------------------------------------------------
# config.py workspace tasks
# ---------------------------------------------------------------------------


class TestParseRepoUrl:
    def test_ssh_url(self) -> None:
        from augint_tools.cli.commands.config import _parse_repo_url

        assert _parse_repo_url("git@github.com:owner1/repo-one.git") == ("owner1", "repo-one")

    def test_ssh_url_no_git_suffix(self) -> None:
        from augint_tools.cli.commands.config import _parse_repo_url

        assert _parse_repo_url("git@github.com:owner1/repo-one") == ("owner1", "repo-one")

    def test_https_url(self) -> None:
        from augint_tools.cli.commands.config import _parse_repo_url

        assert _parse_repo_url("https://github.com/owner2/repo-two.git") == ("owner2", "repo-two")

    def test_https_url_no_git_suffix(self) -> None:
        from augint_tools.cli.commands.config import _parse_repo_url

        assert _parse_repo_url("https://github.com/owner2/repo-two") == ("owner2", "repo-two")

    def test_non_github_url_returns_none(self) -> None:
        from augint_tools.cli.commands.config import _parse_repo_url

        assert _parse_repo_url("https://gitlab.com/owner/repo.git") is None

    def test_empty_string_returns_none(self) -> None:
        from augint_tools.cli.commands.config import _parse_repo_url

        assert _parse_repo_url("") is None


class TestWorkspaceGithubTasks:
    def test_adds_servers_for_workspace_repos(self, tmp_path: Path) -> None:
        from augint_tools.cli.commands.config import ConfigContext, _run_workspace_github_tasks

        # Create workspace.yaml
        (tmp_path / "workspace.yaml").write_text(
            "repos:\n"
            "  - name: repo-one\n"
            "    url: git@github.com:owner1/repo-one.git\n"
            "  - name: repo-two\n"
            "    url: https://github.com/owner2/repo-two.git\n"
        )
        # Create workspace.xml
        idea = tmp_path / ".idea"
        idea.mkdir()
        ws_xml = idea / "workspace.xml"
        ws_xml.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<project version="4"></project>\n'
        )

        c = ConfigContext(
            project_dir=str(tmp_path),
            project_name="test",
            venv_path=str(tmp_path / ".venv"),
            sdk_name="Python 3.12 (test)",
            full_ver="3.12.0",
            major_minor="3.12",
            iml_path=None,
            workspace_path=str(ws_xml),
            misc_path=str(idea / "misc.xml"),
            has_idea=True,
            has_git=True,
            has_venv=False,
            has_pyproject=False,
            git_remote=None,
            win_proj=None,
            win_venv=None,
            win_python=None,
            jb_options=None,
            product_ws=None,
            gh_token="ghp_test123",
            external_project_storage=False,
        )

        result = _run_workspace_github_tasks(c)
        assert result.status == "ok"
        assert "owner1/repo-one" in result.message
        assert "owner2/repo-two" in result.message

        # Verify XML was written
        content = ws_xml.read_text()
        assert "owner1" in content
        assert "repo-one" in content
        assert "owner2" in content
        assert "repo-two" in content

    def test_idempotent_skips_existing(self, tmp_path: Path) -> None:
        from augint_tools.cli.commands.config import ConfigContext, _run_workspace_github_tasks

        (tmp_path / "workspace.yaml").write_text(
            "repos:\n  - name: repo-one\n    url: git@github.com:owner1/repo-one.git\n"
        )
        idea = tmp_path / ".idea"
        idea.mkdir()
        ws_xml = idea / "workspace.xml"
        ws_xml.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<project version="4"></project>\n'
        )

        c = ConfigContext(
            project_dir=str(tmp_path),
            project_name="test",
            venv_path=str(tmp_path / ".venv"),
            sdk_name="Python 3.12 (test)",
            full_ver="3.12.0",
            major_minor="3.12",
            iml_path=None,
            workspace_path=str(ws_xml),
            misc_path=str(idea / "misc.xml"),
            has_idea=True,
            has_git=True,
            has_venv=False,
            has_pyproject=False,
            git_remote=None,
            win_proj=None,
            win_venv=None,
            win_python=None,
            jb_options=None,
            product_ws=None,
            gh_token="",
            external_project_storage=False,
        )

        # Run once
        result1 = _run_workspace_github_tasks(c)
        assert result1.status == "ok"

        # Run again -- should skip
        result2 = _run_workspace_github_tasks(c)
        assert result2.status == "skipped"
        assert "already configured" in result2.message

    def test_empty_repos_list(self, tmp_path: Path) -> None:
        from augint_tools.cli.commands.config import ConfigContext, _run_workspace_github_tasks

        (tmp_path / "workspace.yaml").write_text("repos: []\n")
        idea = tmp_path / ".idea"
        idea.mkdir()
        ws_xml = idea / "workspace.xml"
        ws_xml.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<project version="4"></project>\n'
        )

        c = ConfigContext(
            project_dir=str(tmp_path),
            project_name="test",
            venv_path=str(tmp_path / ".venv"),
            sdk_name="Python 3.12 (test)",
            full_ver="3.12.0",
            major_minor="3.12",
            iml_path=None,
            workspace_path=str(ws_xml),
            misc_path=str(idea / "misc.xml"),
            has_idea=True,
            has_git=True,
            has_venv=False,
            has_pyproject=False,
            git_remote=None,
            win_proj=None,
            win_venv=None,
            win_python=None,
            jb_options=None,
            product_ws=None,
            gh_token="",
            external_project_storage=False,
        )

        result = _run_workspace_github_tasks(c)
        assert result.status == "skipped"
        assert "No repos" in result.message


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


class TestIdeCli:
    def test_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(ide, ["--help"])
        assert result.exit_code == 0
        assert "setup" in result.output
        assert "info" in result.output
        assert "reset" in result.output

    def test_reset_no_product_workspace(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            ide, ["reset", "--project-dir", str(tmp_path), "-y"], obj={"json_mode": True}
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["command"] == "ide reset"
        assert data["result"]["deleted"] is False

    def test_new_command_exists(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["new", "--help"])
        assert result.exit_code == 0
        assert "wizard" in result.output.lower()

    def test_config_yes_on_empty_dir(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["config", "--project-dir", str(tmp_path), "-y"])
        assert result.exit_code == 0, result.output
        assert "Nothing to do" in result.output or "skip" in result.output.lower()
        assert (tmp_path / ".env").read_text() == "GH_ACCOUNT=\nGH_REPO=\n"

    def test_setup_external_storage_skips_iml_steps(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "sample"\n')
        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("version = 3.12.0\n")
        idea = tmp_path / ".idea"
        idea.mkdir()
        (idea / "misc.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<project version="4">\n'
            '  <component name="ExternalStorageConfigurationManager" enabled="true" />\n'
            "</project>\n"
        )
        (idea / "workspace.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<project version="4"></project>\n'
        )

        runner = CliRunner()
        result = runner.invoke(
            ide, ["setup", "--project-dir", str(tmp_path)], obj={"json_mode": True}
        )
        assert result.exit_code in (0, 2), result.output
        data = json.loads(result.output)
        steps = {step["name"]: step for step in data["result"]["steps"]}
        assert steps["module_sdk"]["status"] == "skipped"
        assert "stored externally" in steps["module_sdk"]["message"]
        assert steps["structure"]["status"] == "skipped"
        assert (idea / "sample.iml").exists() is False
        assert "ProjectRootManager" in (idea / "misc.xml").read_text()

    def test_setup_moves_root_iml_into_idea(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "sample"\n')
        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("version = 3.12.0\n")
        idea = tmp_path / ".idea"
        idea.mkdir()
        (idea / "misc.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<project version="4">\n'
            '  <component name="ProjectRootManager" version="2" />\n'
            "</project>\n"
        )
        (tmp_path / "legacy.iml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<module type="PYTHON_MODULE" version="4">\n'
            '  <component name="NewModuleRootManager" inherit-compiler-output="true">\n'
            '    <content url="file://$MODULE_DIR$" />\n'
            '    <orderEntry type="inheritedJdk" />\n'
            "  </component>\n"
            "</module>\n"
        )

        runner = CliRunner()
        result = runner.invoke(
            ide, ["setup", "--project-dir", str(tmp_path)], obj={"json_mode": True}
        )
        assert result.exit_code in (0, 2), result.output
        assert not (tmp_path / "legacy.iml").exists()
        assert (idea / "sample.iml").exists()
        modules_xml = (idea / "modules.xml").read_text()
        assert "$PROJECT_DIR$/.idea/sample.iml" in modules_xml

    def test_config_resilient_on_failing_step(self, tmp_path: Path, monkeypatch) -> None:
        """A step that raises should not stop the wizard."""
        from augint_tools.cli.commands import config as config_module

        original = config_module._run_module_sdk

        def boom(_c):
            raise RuntimeError("simulated failure")

        monkeypatch.setattr(config_module, "_run_module_sdk", boom)
        idea = tmp_path / ".idea"
        idea.mkdir()
        (idea / "test.iml").write_text(
            '<module type="JAVA_MODULE" version="4">'
            '<component name="NewModuleRootManager">'
            '<content url="file://$MODULE_DIR$"/>'
            "</component></module>"
        )
        for step in config_module.CONFIG_STEPS:
            if step.id == "module_sdk":
                step.run = boom
                break

        runner = CliRunner()
        result = runner.invoke(cli, ["config", "--project-dir", str(tmp_path), "-y"])
        assert result.exit_code == 0, result.output
        assert "simulated failure" in result.output

        for step in config_module.CONFIG_STEPS:
            if step.id == "module_sdk":
                step.run = original
                break

    def test_info_json_on_tmp_project(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "sample"\n')
        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("version = 3.12.0\n")

        runner = CliRunner()
        result = runner.invoke(
            ide, ["info", "--project-dir", str(tmp_path)], obj={"json_mode": True}
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["command"] == "ide info"
        assert data["scope"] == "ide"
        assert data["result"]["project_name"] == "sample"
        assert data["result"]["sdk_name"] == "Python 3.12 (sample)"

    def test_setup_dry_run_on_empty_project_partial(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "empty"\n')
        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("version = 3.12.0\n")

        runner = CliRunner()
        result = runner.invoke(
            ide,
            ["setup", "--project-dir", str(tmp_path), "--dry-run"],
            obj={"json_mode": True},
        )
        assert result.exit_code in (1, 2, 4), result.output
        data = json.loads(result.output)
        assert data["command"] == "ide setup"
        assert data["result"]["dry_run"] is True
        assert not (tmp_path / ".idea").exists()

    def test_setup_rejects_unknown_skip(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            ide,
            ["setup", "--project-dir", str(tmp_path), "--skip", "bogus", "--dry-run"],
            obj={"json_mode": True},
        )
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert "Unknown --skip" in data["summary"]
