"""Tests for env variable classification."""

import pytest

from augint_tools.env.classify import (
    Classification,
    _is_infra_key,
    _is_safe_value,
    _parse_env_comments,
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


class TestEmptyValueSkip:
    def test_empty_string_classified_as_skip(self):
        r = classify_variable("STAGING_VAR", "")
        assert r.classification == Classification.SKIP
        assert "empty value" in r.reasons

    def test_classify_env_skips_empty_values(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(
            'NORMAL_VAR=hello\nSTAGING_VAR=\nEMPTY_QUOTED=""\nSECRET_TOKEN=ghp_abc123\n'
        )
        results = classify_env(str(env_file))
        classifications = {r.key: r.classification for r in results}
        assert classifications["NORMAL_VAR"] == Classification.VARIABLE
        assert classifications.get("STAGING_VAR") == Classification.SKIP
        assert classifications["SECRET_TOKEN"] == Classification.SECRET

    def test_partition_env_excludes_empty_values(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("NORMAL_VAR=hello\nSTAGING_VAR=\nSECRET_TOKEN=ghp_abc123\n")
        secrets, variables = partition_env(str(env_file))
        assert "STAGING_VAR" not in secrets
        assert "STAGING_VAR" not in variables
        assert "NORMAL_VAR" in variables
        assert "SECRET_TOKEN" in secrets


class TestMultipleReasons:
    def test_key_and_value_match(self):
        r = classify_variable("GH_TOKEN", "ghp_abc123def456ghi789jkl012mno345pqr678stu")
        assert r.classification == Classification.SECRET
        assert len(r.reasons) >= 2


# ---------------------------------------------------------------------------
# NEW: Safe-value pattern tests (ARNs, URLs, buckets, slugs)
# ---------------------------------------------------------------------------


class TestSafeValuePatterns:
    """Values that look like infrastructure identifiers should be VARIABLE, not SECRET."""

    def test_arn_iam_role(self):
        r = classify_variable(
            "STAGING_AWS_DEPLOY_ROLE",
            "arn:aws:iam::123456789012:role/deploy-role",
        )
        assert r.classification == Classification.VARIABLE

    def test_arn_pipeline_execution_role(self):
        r = classify_variable(
            "STAGING_PIPELINE_EXECUTION_ROLE",
            "arn:aws:iam::123456789012:role/pipeline-exec",
        )
        assert r.classification == Classification.VARIABLE

    def test_arn_cloudformation_execution_role(self):
        r = classify_variable(
            "STAGING_CLOUDFORMATION_EXECUTION_ROLE",
            "arn:aws:iam::123456789012:role/cf-exec",
        )
        assert r.classification == Classification.VARIABLE

    def test_arn_wildcard_cert(self):
        r = classify_variable(
            "STAGING_WILDCARD_CERT_ARN",
            "arn:aws:acm:us-east-1:123456789012:certificate/abc-123-def",
        )
        assert r.classification == Classification.VARIABLE

    def test_arn_cdk_certificate(self):
        r = classify_variable(
            "STAGING_CDK_CERTIFICATE_ARN",
            "arn:aws:acm:us-east-1:123456789012:certificate/xyz-789",
        )
        assert r.classification == Classification.VARIABLE

    def test_s3_bucket_name(self):
        r = classify_variable(
            "STAGING_ARTIFACTS_BUCKET",
            "my-staging-artifacts-bucket-123",
        )
        assert r.classification == Classification.VARIABLE

    def test_cors_origin_url(self):
        r = classify_variable(
            "HWH_ALLOWED_ORIGINS",
            "https://example.com",
        )
        assert r.classification == Classification.VARIABLE

    def test_cors_origin_url_multiple(self):
        """Multiple origins as a comma-separated string (starts with https)."""
        r = classify_variable(
            "JACKSON_ALLOWED_ORIGINS",
            "https://app.example.com",
        )
        assert r.classification == Classification.VARIABLE

    def test_prod_allowed_origins(self):
        r = classify_variable(
            "PROD_ALLOWED_ORIGINS",
            "https://prod.example.com",
        )
        assert r.classification == Classification.VARIABLE

    def test_repo_slug(self):
        r = classify_variable("GH_REPO", "augint-tools")
        assert r.classification == Classification.VARIABLE

    def test_project_name(self):
        r = classify_variable("PROJECT_NAME", "my-project")
        assert r.classification == Classification.VARIABLE

    def test_arn_does_not_override_value_prefix_secret(self):
        """An ARN value should NOT override known secret prefixes like AKIA."""
        r = classify_variable("CLOUD_ID", "AKIAIOSFODNN7EXAMPLE")
        assert r.classification == Classification.SECRET

    def test_url_value_with_secret_key(self):
        """A URL value should override key-keyword 'auth' for endpoint URLs."""
        r = classify_variable("AUTH_ENDPOINT", "https://auth.example.com/oauth")
        assert r.classification == Classification.VARIABLE

    def test_real_secret_still_detected(self):
        """Actual credential values must still be classified as SECRET."""
        r = classify_variable("MY_SECRET", "super-secret-password-123!")
        assert r.classification == Classification.SECRET


class TestInfraKeySuffix:
    """Keys ending with infrastructure suffixes should not trigger on embedded keywords."""

    def test_deploy_role_not_secret(self):
        """'_role' suffix suppresses 'key'-like keyword matches."""
        reason = _is_infra_key("STAGING_AWS_DEPLOY_ROLE")
        assert reason is not None
        assert "_role" in reason

    def test_bucket_suffix(self):
        reason = _is_infra_key("STAGING_ARTIFACTS_BUCKET")
        assert reason is not None

    def test_cert_arn_suffix(self):
        reason = _is_infra_key("STAGING_WILDCARD_CERT_ARN")
        assert reason is not None

    def test_url_suffix(self):
        reason = _is_infra_key("DATABASE_URL")
        assert reason is not None

    def test_no_match(self):
        reason = _is_infra_key("API_KEY")
        assert reason is None

    def test_auth_key_with_endpoint_suffix(self):
        reason = _is_infra_key("AUTH_ENDPOINT")
        assert reason is not None
        assert "_endpoint" in reason


class TestIsSafeValue:
    def test_arn_recognized(self):
        assert _is_safe_value("arn:aws:iam::123456789012:role/deploy") is not None

    def test_arn_govcloud(self):
        assert _is_safe_value("arn:aws-us-gov:s3:::my-bucket") is not None

    def test_url_http(self):
        assert _is_safe_value("http://localhost:3000") is not None

    def test_url_https(self):
        assert _is_safe_value("https://example.com") is not None

    def test_bucket_name_not_safe_by_value(self):
        """Bucket names are handled by key-suffix, not value pattern."""
        assert _is_safe_value("my-staging-bucket-123") is None

    def test_not_safe_random_string(self):
        assert _is_safe_value("aB3$kL9#mN2@pQ5&rT8") is None

    def test_not_safe_github_pat(self):
        assert _is_safe_value("ghp_abc123def456") is None


# ---------------------------------------------------------------------------
# NEW: Comment-hint tests
# ---------------------------------------------------------------------------


class TestParseEnvComments:
    def test_preceding_line_var(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("# @var\nMY_SECRET=some-value\n")
        hints = _parse_env_comments(str(env_file))
        assert hints.get("MY_SECRET") == "var"

    def test_preceding_line_secret(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("# @secret\nAPP_NAME=myapp\n")
        hints = _parse_env_comments(str(env_file))
        assert hints.get("APP_NAME") == "secret"

    def test_trailing_hint_var(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("MY_SECRET=some-value # var\n")
        hints = _parse_env_comments(str(env_file))
        assert hints.get("MY_SECRET") == "var"

    def test_trailing_hint_secret(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("APP_NAME=myapp # secret\n")
        hints = _parse_env_comments(str(env_file))
        assert hints.get("APP_NAME") == "secret"

    def test_trailing_overrides_preceding(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("# @secret\nAPP_NAME=myapp # var\n")
        hints = _parse_env_comments(str(env_file))
        assert hints.get("APP_NAME") == "var"

    def test_no_hints(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("APP_NAME=myapp\nDB_HOST=localhost\n")
        hints = _parse_env_comments(str(env_file))
        assert hints == {}

    def test_missing_file(self, tmp_path):
        hints = _parse_env_comments(str(tmp_path / "nope.env"))
        assert hints == {}

    def test_hint_does_not_bleed(self, tmp_path):
        """A preceding-line hint only applies to the next KEY=value line."""
        env_file = tmp_path / ".env"
        env_file.write_text("# @secret\nFIRST=a\nSECOND=b\n")
        hints = _parse_env_comments(str(env_file))
        assert hints.get("FIRST") == "secret"
        assert "SECOND" not in hints


class TestCommentHintClassification:
    """Integration: comment hints flow through classify_env and partition_env."""

    def test_var_hint_overrides_keyword(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("# @var\nMY_SECRET=some-value\n")
        results = classify_env(str(env_file))
        assert results[0].classification == Classification.VARIABLE
        assert "inline comment hint @var" in results[0].reasons

    def test_secret_hint_overrides_plain(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("# @secret\nAPP_NAME=myapp\n")
        results = classify_env(str(env_file))
        assert results[0].classification == Classification.SECRET
        assert "inline comment hint @secret" in results[0].reasons

    def test_trailing_var_hint(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("MY_SECRET=some-value # var\n")
        results = classify_env(str(env_file))
        assert results[0].classification == Classification.VARIABLE

    def test_partition_with_hints(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("# @var\nMY_SECRET=overridden\n# @secret\nAPP_NAME=forced-secret\n")
        secrets, variables = partition_env(str(env_file))
        assert "MY_SECRET" in variables
        assert "APP_NAME" in secrets


# ---------------------------------------------------------------------------
# NEW: Force-override tests
# ---------------------------------------------------------------------------


class TestForceOverrides:
    def test_force_var_overrides_keyword(self):
        r = classify_variable("MY_SECRET", "some-value", force_var=frozenset({"MY_SECRET"}))
        assert r.classification == Classification.VARIABLE
        assert "--force-var override" in r.reasons

    def test_force_secret_overrides_plain(self):
        r = classify_variable("APP_NAME", "myapp", force_secret=frozenset({"APP_NAME"}))
        assert r.classification == Classification.SECRET
        assert "--force-secret override" in r.reasons

    def test_force_secret_beats_force_var(self):
        """If both are specified for the same key, force-secret wins."""
        r = classify_variable(
            "SHARED",
            "value",
            force_var=frozenset({"SHARED"}),
            force_secret=frozenset({"SHARED"}),
        )
        assert r.classification == Classification.SECRET

    def test_force_overrides_in_classify_env(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("MY_SECRET=val\nAPP_NAME=myapp\n")
        results = classify_env(
            str(env_file),
            force_var=frozenset({"MY_SECRET"}),
            force_secret=frozenset({"APP_NAME"}),
        )
        classifications = {r.key: r.classification for r in results}
        assert classifications["MY_SECRET"] == Classification.VARIABLE
        assert classifications["APP_NAME"] == Classification.SECRET

    def test_force_overrides_in_partition_env(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("MY_SECRET=val\nAPP_NAME=myapp\n")
        secrets, variables = partition_env(
            str(env_file),
            force_var=frozenset({"MY_SECRET"}),
            force_secret=frozenset({"APP_NAME"}),
        )
        assert "MY_SECRET" in variables
        assert "APP_NAME" in secrets

    def test_force_secret_overrides_comment_hint(self, tmp_path):
        """CLI --force-secret beats an inline # @var comment."""
        env_file = tmp_path / ".env"
        env_file.write_text("# @var\nMY_SECRET=val\n")
        results = classify_env(str(env_file), force_secret=frozenset({"MY_SECRET"}))
        assert results[0].classification == Classification.SECRET
        assert "--force-secret override" in results[0].reasons


# ---------------------------------------------------------------------------
# NEW: Regression tests for the originally miscategorized keys
# ---------------------------------------------------------------------------


class TestRegressionMiscategorizedKeys:
    """Every key listed in the bug report must now be a VARIABLE."""

    @pytest.mark.parametrize(
        "key,value",
        [
            (
                "STAGING_AWS_DEPLOY_ROLE",
                "arn:aws:iam::111111111111:role/staging-deploy",
            ),
            (
                "STAGING_PIPELINE_EXECUTION_ROLE",
                "arn:aws:iam::111111111111:role/pipeline-exec",
            ),
            (
                "STAGING_CLOUDFORMATION_EXECUTION_ROLE",
                "arn:aws:iam::111111111111:role/cf-exec",
            ),
            (
                "STAGING_WILDCARD_CERT_ARN",
                "arn:aws:acm:us-east-1:111111111111:certificate/abc-def",
            ),
            (
                "STAGING_CDK_CERTIFICATE_ARN",
                "arn:aws:acm:us-east-1:111111111111:certificate/xyz-789",
            ),
            (
                "STAGING_ARTIFACTS_BUCKET",
                "my-staging-artifacts-bucket",
            ),
            (
                "HWH_ALLOWED_ORIGINS",
                "https://hwh.example.com",
            ),
            (
                "JACKSON_ALLOWED_ORIGINS",
                "https://jackson.example.com",
            ),
            (
                "PROD_ALLOWED_ORIGINS",
                "https://prod.example.com",
            ),
            ("GH_REPO", "augint-tools"),
            ("PROJECT_NAME", "my-project"),
        ],
    )
    def test_infra_identifier_is_variable(self, key, value):
        r = classify_variable(key, value)
        assert r.classification == Classification.VARIABLE, (
            f"{key}={value!r} classified as {r.classification.value}; reasons={r.reasons}"
        )
