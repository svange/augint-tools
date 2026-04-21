"""Tests for team_secrets.models dataclasses."""

from augint_tools.team_secrets.models import (
    ConflictEntry,
    DoctorCheck,
    MergeResult,
    ProjectConfig,
    TeamConfig,
    UserRecord,
)


def test_team_config_frozen():
    config = TeamConfig(name="woxom", org="augmenting-integrations", username="sam")
    assert config.name == "woxom"
    assert config.org == "augmenting-integrations"
    assert config.username == "sam"


def test_project_config_defaults():
    pc = ProjectConfig(name="myapp", repo="org/myapp", description="test app")
    assert pc.environments == ["dev", "prod"]


def test_user_record():
    user = UserRecord(name="alice", public_key="age1abc123")
    assert user.name == "alice"
    assert user.public_key == "age1abc123"


def test_conflict_entry():
    c = ConflictEntry(key="DB_URL", local_value="local", team_value="team")
    assert c.key == "DB_URL"


def test_merge_result():
    mr = MergeResult(
        merged={"A": "1"},
        additions=["A"],
        conflicts=[],
        unchanged=[],
    )
    assert mr.merged == {"A": "1"}
    assert mr.additions == ["A"]


def test_doctor_check():
    check = DoctorCheck(name="sops", status="pass", message="ok")
    assert check.status == "pass"
