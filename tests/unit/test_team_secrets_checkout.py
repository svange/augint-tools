"""Tests for team_secrets.checkout module."""

from augint_tools.team_secrets.checkout import DEFAULT_ORG, secrets_repo_slug


def test_secrets_repo_slug_default_org():
    assert secrets_repo_slug("woxom") == "augmenting-integrations/woxom-secrets"


def test_secrets_repo_slug_custom_org():
    assert secrets_repo_slug("woxom", "my-org") == "my-org/woxom-secrets"


def test_default_org():
    assert DEFAULT_ORG == "augmenting-integrations"
