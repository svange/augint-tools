"""Tests for team_secrets.recipients module."""

from augint_tools.team_secrets.models import UserRecord
from augint_tools.team_secrets.recipients import (
    add_recipient,
    collect_project_recipients,
    collect_team_recipients,
    generate_sops_yaml,
    read_recipients_file,
    remove_recipient,
    write_recipients_file,
)


def test_read_recipients_file_empty(tmp_path):
    path = tmp_path / "empty.txt"
    assert read_recipients_file(path) == []


def test_read_recipients_file(tmp_path):
    path = tmp_path / "team.txt"
    path.write_text("# alice\nage1abc123\n\n# bob\nage1def456\n")
    records = read_recipients_file(path)
    assert len(records) == 2
    assert records[0].name == "alice"
    assert records[0].public_key == "age1abc123"
    assert records[1].name == "bob"
    assert records[1].public_key == "age1def456"


def test_read_recipients_file_no_names(tmp_path):
    path = tmp_path / "team.txt"
    path.write_text("age1abc123\nage1def456\n")
    records = read_recipients_file(path)
    assert len(records) == 2
    assert records[0].name == ""
    assert records[0].public_key == "age1abc123"


def test_write_recipients_file(tmp_path):
    path = tmp_path / "out.txt"
    records = [
        UserRecord(name="alice", public_key="age1abc"),
        UserRecord(name="bob", public_key="age1def"),
    ]
    write_recipients_file(path, records)
    content = path.read_text()
    assert "# alice" in content
    assert "age1abc" in content
    assert "# bob" in content
    assert "age1def" in content


def test_add_recipient(tmp_path):
    path = tmp_path / "team.txt"
    path.write_text("# alice\nage1abc\n")
    user = UserRecord(name="bob", public_key="age1def")
    add_recipient(path, user)
    records = read_recipients_file(path)
    assert len(records) == 2


def test_add_recipient_duplicate(tmp_path):
    path = tmp_path / "team.txt"
    path.write_text("# alice\nage1abc\n")
    user = UserRecord(name="alice-dupe", public_key="age1abc")
    add_recipient(path, user)
    records = read_recipients_file(path)
    assert len(records) == 1  # Not added again


def test_remove_recipient(tmp_path):
    path = tmp_path / "team.txt"
    path.write_text("# alice\nage1abc\n\n# bob\nage1def\n")
    assert remove_recipient(path, "alice") is True
    records = read_recipients_file(path)
    assert len(records) == 1
    assert records[0].name == "bob"


def test_remove_recipient_not_found(tmp_path):
    path = tmp_path / "team.txt"
    path.write_text("# alice\nage1abc\n")
    assert remove_recipient(path, "nobody") is False


def test_collect_team_recipients(tmp_path):
    recipients_dir = tmp_path / "recipients"
    recipients_dir.mkdir()
    (recipients_dir / "team-woxom.txt").write_text("# sam\nage1sam\n\n# alice\nage1alice\n")
    keys = collect_team_recipients(recipients_dir, "woxom")
    assert "age1sam" in keys
    assert "age1alice" in keys


def test_collect_project_recipients(tmp_path):
    recipients_dir = tmp_path / "recipients"
    recipients_dir.mkdir()
    (recipients_dir / "team-woxom.txt").write_text("# sam\nage1sam\n")
    (recipients_dir / "project-myapp.txt").write_text("# contractor\nage1contractor\n")
    keys = collect_project_recipients(recipients_dir, "woxom", "myapp")
    assert "age1sam" in keys
    assert "age1contractor" in keys


def test_generate_sops_yaml_no_recipients(tmp_path):
    recipients_dir = tmp_path / "recipients"
    recipients_dir.mkdir()
    (recipients_dir / "team-woxom.txt").write_text("# empty\n")
    content = generate_sops_yaml(tmp_path, "woxom")
    assert "creation_rules:" in content
    assert "No recipients configured" in content


def test_generate_sops_yaml_with_recipients(tmp_path):
    recipients_dir = tmp_path / "recipients"
    recipients_dir.mkdir()
    (recipients_dir / "team-woxom.txt").write_text("# sam\nage1sam123\n\n# alice\nage1alice456\n")
    content = generate_sops_yaml(tmp_path, "woxom")
    assert "age1sam123" in content
    assert "age1alice456" in content
    assert "path_regex" in content


def test_generate_sops_yaml_project_override(tmp_path):
    recipients_dir = tmp_path / "recipients"
    recipients_dir.mkdir()
    (recipients_dir / "team-woxom.txt").write_text("# sam\nage1sam\n")
    (recipients_dir / "project-special.txt").write_text("# extra\nage1extra\n")
    content = generate_sops_yaml(tmp_path, "woxom")
    # Should have project-specific rule because keys differ
    assert "Project: special" in content
    assert "age1extra" in content
