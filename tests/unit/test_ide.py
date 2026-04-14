"""Tests for IDE setup helpers, steps, and CLI."""

from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from click.testing import CliRunner

from augint_tools.cli.__main__ import cli
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
    detect_project_name,
    detect_python_version,
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
            build_bookmarks_xml,
            inject_bookmarks,
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
        component = build_bookmarks_xml(slots, str(tmp_path), group_name="test")
        result = inject_bookmarks(ws_path, component)
        assert result["action"] == "created"

        content = Path(ws_path).read_text()
        assert "BookmarksManager" in content
        assert "DIGIT_1" in content
        assert "pyproject.toml" in content

    def test_bookmarks_already_set(self, tmp_path: Path) -> None:
        from augint_tools.ide.bookmarks import (
            BookmarkSlot,
            bookmarks_already_set,
            build_bookmarks_xml,
            inject_bookmarks,
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
        component = build_bookmarks_xml(slots, str(tmp_path))
        inject_bookmarks(ws_path, component)

        assert bookmarks_already_set(ws_path, slots, str(tmp_path))

    def test_step_bookmarks_no_workspace_file(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\n')
        res = step_bookmarks(str(tmp_path), "x", None, None)
        assert res.status == "action-required"
        assert "product_workspace_file" in res.missing_inputs

    def test_step_bookmarks_no_files(self, tmp_path: Path) -> None:
        res = step_bookmarks(str(tmp_path), "x", None, None)
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
# CLI integration
# ---------------------------------------------------------------------------


class TestIdeCli:
    def test_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["ide", "--help"])
        assert result.exit_code == 0
        assert "setup" in result.output
        assert "info" in result.output

    def test_info_json_on_tmp_project(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "sample"\n')
        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("version = 3.12.0\n")

        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "ide", "info", "--project-dir", str(tmp_path)])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["command"] == "ide info"
        assert data["scope"] == "ide"
        assert data["result"]["project_name"] == "sample"
        assert data["result"]["sdk_name"] == "Python 3.12 (sample)"

    def test_setup_dry_run_on_empty_project_partial(self, tmp_path: Path) -> None:
        # No .idea/, no .iml — dry run should surface errors without writing.
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "empty"\n')
        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("version = 3.12.0\n")

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "ide", "setup", "--project-dir", str(tmp_path), "--dry-run"],
        )
        # error + action-required present -> exit 4 (partial)
        assert result.exit_code in (1, 2, 4), result.output
        data = json.loads(result.output)
        assert data["command"] == "ide setup"
        assert data["result"]["dry_run"] is True
        # No XML should have been written
        assert not (tmp_path / ".idea").exists()

    def test_setup_rejects_unknown_skip(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--json",
                "ide",
                "setup",
                "--project-dir",
                str(tmp_path),
                "--skip",
                "bogus",
                "--dry-run",
            ],
        )
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert "Unknown --skip" in data["summary"]
