from __future__ import annotations

import configparser
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from augint_tools.dashboard import awsprobe


def test_is_safe_profile_name():
    assert awsprobe._is_safe_profile_name("default")
    assert awsprobe._is_safe_profile_name("team.prod_1")
    assert not awsprobe._is_safe_profile_name("bad;name")


def test_parse_config_for_profile_with_sso_session():
    config = configparser.ConfigParser()
    config["profile app"] = {"region": "us-east-1", "sso_session": "corp"}
    config["sso-session corp"] = {"sso_start_url": "https://example.awsapps.com/start"}
    parsed = awsprobe._parse_config_for_profile(config, "app")
    assert parsed["region"] == "us-east-1"
    assert parsed["sso_start_url"] == "https://example.awsapps.com/start"


def test_load_sso_token_cache_ignores_bad_files(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(awsprobe.Path, "home", lambda: tmp_path)
    cache_dir = tmp_path / ".aws" / "sso" / "cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "good.json").write_text(
        json.dumps({"startUrl": "https://example", "expiresAt": "2099-01-01T00:00:00Z"})
    )
    (cache_dir / "bad.json").write_text("{ not json")
    loaded = awsprobe._load_sso_token_cache()
    assert loaded == {"https://example": "2099-01-01T00:00:00Z"}


def test_sso_token_expired():
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    assert awsprobe._sso_token_expired("not-a-date")
    assert awsprobe._sso_token_expired(past)
    assert not awsprobe._sso_token_expired(future)


def test_list_aws_profiles_returns_sorted_names(monkeypatch, tmp_path: Path):
    config_path = tmp_path / "config"
    config_path.write_text(
        "[default]\nregion=us-east-1\n[profile zeta]\nregion=us-east-1\n[profile alpha]\n"
    )
    monkeypatch.setattr(awsprobe, "_aws_config_path", lambda: config_path)
    assert awsprobe.list_aws_profiles() == ["alpha", "default", "zeta"]


def test_probe_aws_local_without_cli(monkeypatch):
    monkeypatch.setattr(awsprobe, "_is_aws_cli_available", lambda: False)
    monkeypatch.setattr(awsprobe, "list_aws_profiles", lambda: ["dev"])
    monkeypatch.setattr(
        awsprobe,
        "_parse_config_for_profile",
        lambda *_: {
            "region": None,
            "sso_start_url": None,
            "sso_account_id": None,
            "sso_role_name": None,
        },
    )
    state = awsprobe.probe_aws_local()
    assert state.aws_cli_available is False
    assert state.profiles[0].status == "unknown"
    assert state.profiles[0].error == "aws CLI not found"


def test_probe_aws_local_keeps_non_sso_previous_state(monkeypatch):
    monkeypatch.setattr(awsprobe, "_is_aws_cli_available", lambda: True)
    monkeypatch.setattr(awsprobe, "list_aws_profiles", lambda: ["plain"])
    monkeypatch.setattr(awsprobe, "_load_sso_token_cache", lambda: {})
    monkeypatch.setattr(
        awsprobe,
        "_parse_config_for_profile",
        lambda *_: {
            "region": "us-east-1",
            "sso_start_url": None,
            "sso_account_id": None,
            "sso_role_name": None,
        },
    )
    previous = awsprobe.AwsState(
        profiles=(
            awsprobe.AwsProfile(
                name="plain",
                region="us-east-1",
                sso_start_url=None,
                sso_account_id=None,
                sso_role_name=None,
                account_id="123456789012",
                user_arn="arn:aws:iam::123456789012:user/me",
                status="active",
                error=None,
            ),
        ),
        aws_cli_available=True,
        last_check_at=None,
    )
    state = awsprobe.probe_aws_local(previous)
    assert state.profiles[0].status == "active"
    assert state.profiles[0].account_id == "123456789012"


def test_probe_aws_local_sso_missing_and_expired_tokens(monkeypatch):
    monkeypatch.setattr(awsprobe, "_is_aws_cli_available", lambda: True)
    monkeypatch.setattr(awsprobe, "list_aws_profiles", lambda: ["missing", "expired"])
    monkeypatch.setattr(
        awsprobe,
        "_parse_config_for_profile",
        lambda _, name: {
            "region": "us-east-1",
            "sso_start_url": f"https://{name}.example",
            "sso_account_id": None,
            "sso_role_name": None,
        },
    )
    monkeypatch.setattr(
        awsprobe,
        "_load_sso_token_cache",
        lambda: {"https://expired.example": "2000-01-01T00:00:00Z"},
    )
    monkeypatch.setattr(awsprobe, "_sso_token_expired", lambda _: True)
    state = awsprobe.probe_aws_local()
    assert [p.status for p in state.profiles] == ["expired", "expired"]
    assert state.profiles[0].error == "no SSO token cached"
    assert state.profiles[1].error == "SSO token expired"


def test_probe_aws_local_sso_active_carries_identity(monkeypatch):
    monkeypatch.setattr(awsprobe, "_is_aws_cli_available", lambda: True)
    monkeypatch.setattr(awsprobe, "list_aws_profiles", lambda: ["app"])
    monkeypatch.setattr(
        awsprobe,
        "_parse_config_for_profile",
        lambda *_: {
            "region": "us-east-1",
            "sso_start_url": "https://app.example",
            "sso_account_id": "123",
            "sso_role_name": "role",
        },
    )
    monkeypatch.setattr(
        awsprobe, "_load_sso_token_cache", lambda: {"https://app.example": "2099-01-01T00:00:00Z"}
    )
    monkeypatch.setattr(awsprobe, "_sso_token_expired", lambda _: False)
    previous = awsprobe.AwsState(
        profiles=(
            awsprobe.AwsProfile(
                name="app",
                region="us-east-1",
                sso_start_url="https://app.example",
                sso_account_id="123",
                sso_role_name="role",
                account_id="123456789012",
                user_arn="arn:aws:iam::123456789012:role/role",
                status="active",
                error=None,
            ),
        ),
        aws_cli_available=True,
        last_check_at=None,
    )
    state = awsprobe.probe_aws_local(previous)
    profile = state.profiles[0]
    assert profile.status == "active"
    assert profile.account_id == "123456789012"
    assert profile.user_arn is not None


def test_save_and_load_aws_cache_round_trip(monkeypatch, tmp_path: Path):
    cache_dir = tmp_path / "cache"
    cache_file = cache_dir / "aws_cache.json"
    monkeypatch.setattr(awsprobe, "_CACHE_DIR", cache_dir)
    monkeypatch.setattr(awsprobe, "_CACHE_FILE", cache_file)
    state = awsprobe.AwsState(
        profiles=(
            awsprobe.AwsProfile(
                name="dev",
                region="us-east-1",
                sso_start_url=None,
                sso_account_id=None,
                sso_role_name=None,
                account_id=None,
                user_arn=None,
                status="unknown",
                error="not verified",
            ),
        ),
        aws_cli_available=True,
        last_check_at="2026-01-01T00:00:00+00:00",
    )
    awsprobe.save_aws_cache(state)
    loaded = awsprobe.load_aws_cache()
    assert loaded == state


def test_load_aws_cache_invalid_json_returns_none(monkeypatch, tmp_path: Path):
    cache_file = tmp_path / "aws_cache.json"
    cache_file.write_text("{bad json")
    monkeypatch.setattr(awsprobe, "_CACHE_FILE", cache_file)
    assert awsprobe.load_aws_cache() is None


def test_launch_sso_login_validation_and_spawn(monkeypatch):
    popen = MagicMock(return_value=SimpleNamespace())
    monkeypatch.setattr(awsprobe.subprocess, "Popen", popen)
    monkeypatch.setattr(awsprobe, "_is_aws_cli_available", lambda: True)
    assert not awsprobe.launch_sso_login("bad;name")
    assert awsprobe.launch_sso_login("dev")
    popen.assert_called_once()
