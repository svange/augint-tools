"""Tests for env variable classification."""

import pytest

from augint_tools.env.classify import (
    Classification,
    classify_env,
    classify_variable,
    partition_env,
)


class TestKeywordDetection:
    def test_token_in_key(self):
        r = classify_variable("GH_TOKEN", "ghp_abc123")
        assert r.classification == Classification.SECRET

    def test_secret_in_key(self):
        r = classify_variable("MY_SECRET", "some-value")
        assert r.classification == Classification.SECRET

    def test_password_in_key(self):
        r = classify_variable("DB_PASSWORD", "hunter2")
        assert r.classification == Classification.SECRET

    def test_key_in_key(self):
        r = classify_variable("API_KEY", "abcdef1234567890")
        assert r.classification == Classification.SECRET

    def test_auth_in_key(self):
        r = classify_variable("AUTH_HEADER", "Bearer xyz")
        assert r.classification == Classification.SECRET

    def test_case_insensitive(self):
        r = classify_variable("my_Token_value", "abc")
        assert r.classification == Classification.SECRET

    def test_plain_variable(self):
        r = classify_variable("APP_NAME", "myapp")
        assert r.classification == Classification.VARIABLE

    def test_plain_url(self):
        r = classify_variable("DATABASE_URL", "localhost")
        assert r.classification == Classification.VARIABLE


class TestValuePrefixDetection:
    def test_github_pat(self):
        r = classify_variable("MY_VAR", "ghp_1234567890abcdef1234567890abcdef12345678")
        assert r.classification == Classification.SECRET
        assert any("ghp_" in reason for reason in r.reasons)

    def test_stripe_secret_key(self):
        r = classify_variable("PAYMENT_CONFIG", "sk_live_abc123def456ghi789")
        assert r.classification == Classification.SECRET

    def test_slack_token(self):
        r = classify_variable("CHAT_CONFIG", "xoxb-1234-5678-abcdef")
        assert r.classification == Classification.SECRET

    def test_aws_access_key(self):
        r = classify_variable("CLOUD_ID", "AKIAIOSFODNN7EXAMPLE")
        assert r.classification == Classification.SECRET
        assert any("AKIA" in reason for reason in r.reasons)

    def test_github_pat_new_format(self):
        r = classify_variable("CI_CREDENTIAL", "github_pat_abc123def456")
        assert r.classification == Classification.SECRET


class TestEntropyDetection:
    def test_high_entropy_secret(self):
        r = classify_variable("CONFIG_VALUE", "aB3$kL9#mN2@pQ5&rT8")
        assert r.classification == Classification.SECRET
        assert any("entropy" in reason for reason in r.reasons)

    def test_low_entropy_variable(self):
        r = classify_variable("APP_NAME", "production")
        assert r.classification == Classification.VARIABLE

    def test_short_value_skips_entropy(self):
        r = classify_variable("FLAG", "true")
        assert r.classification == Classification.VARIABLE


class TestPatternDetection:
    def test_jwt_pattern(self):
        jwt = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
            ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
            ".dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        )
        r = classify_variable("SESSION_DATA", jwt)
        assert r.classification == Classification.SECRET
        assert any("JWT" in reason for reason in r.reasons)

    def test_long_hex_string(self):
        r = classify_variable("BUILD_ID", "a" * 40)
        assert r.classification == Classification.SECRET
        assert any("hex" in reason for reason in r.reasons)

    def test_base64_blob(self):
        r = classify_variable("ENCODED_DATA", "SGVsbG9Xb3JsZEhlbGxvV29ybGQ=")
        assert r.classification == Classification.SECRET
        assert any("base64" in reason for reason in r.reasons)


class TestSkipList:
    def test_aws_profile_skipped(self):
        r = classify_variable("AWS_PROFILE", "default")
        assert r.classification == Classification.SKIP

    def test_aws_default_region_skipped(self):
        r = classify_variable("AWS_DEFAULT_REGION", "us-east-1")
        assert r.classification == Classification.SKIP


class TestClassifyEnv:
    def test_reads_env_file(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("APP_NAME=myapp\nGH_TOKEN=ghp_abc123\nAWS_PROFILE=default\n")
        results = classify_env(str(env_file))
        assert len(results) == 3
        classifications = {r.key: r.classification for r in results}
        assert classifications["APP_NAME"] == Classification.VARIABLE
        assert classifications["GH_TOKEN"] == Classification.SECRET
        assert classifications["AWS_PROFILE"] == Classification.SKIP

    def test_missing_file(self):
        with pytest.raises(FileNotFoundError):
            classify_env("/nonexistent/.env")

    def test_empty_file(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("")
        results = classify_env(str(env_file))
        assert results == []


class TestPartitionEnv:
    def test_partitions_correctly(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(
            "APP_NAME=myapp\n"
            "GH_TOKEN=ghp_abc123\n"
            "DB_HOST=localhost\n"
            "API_KEY=supersecret\n"
            "AWS_PROFILE=default\n"
        )
        secrets, variables = partition_env(str(env_file))
        assert "GH_TOKEN" in secrets
        assert "API_KEY" in secrets
        assert "APP_NAME" in variables
        assert "DB_HOST" in variables
        assert "AWS_PROFILE" not in secrets
        assert "AWS_PROFILE" not in variables


class TestMultipleReasons:
    def test_key_and_value_match(self):
        r = classify_variable("GH_TOKEN", "ghp_abc123def456ghi789jkl012mno345pqr678stu")
        assert r.classification == Classification.SECRET
        assert len(r.reasons) >= 2
