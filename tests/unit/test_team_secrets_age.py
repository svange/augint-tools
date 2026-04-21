"""Tests for team_secrets.age module."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from augint_tools.team_secrets.age import (
    decrypt_with_password,
    encrypt_with_password,
    generate_keypair,
    is_age_installed,
)


def test_is_age_installed_true():
    with patch("augint_tools.team_secrets.age.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        assert is_age_installed() is True
        assert mock_run.call_count == 2  # age --version + age-keygen --version


def test_is_age_installed_false_not_found():
    with patch("augint_tools.team_secrets.age.subprocess.run") as mock_run:
        mock_run.side_effect = FileNotFoundError()
        assert is_age_installed() is False


def test_is_age_installed_false_error():
    with patch("augint_tools.team_secrets.age.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.CalledProcessError(1, "age")
        assert is_age_installed() is False


def test_generate_keypair():
    fake_output = (
        "# created: 2026-04-21T10:00:00Z\n"
        "# public key: age1testpubkey123\n"
        "AGE-SECRET-KEY-1TESTPRIVATEKEY456\n"
    )
    with patch("augint_tools.team_secrets.age.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=fake_output,
        )
        kp = generate_keypair()
        assert kp.public_key == "age1testpubkey123"
        assert "AGE-SECRET-KEY" in kp.private_key


def test_generate_keypair_no_pubkey():
    with patch("augint_tools.team_secrets.age.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="no key here\n")
        with pytest.raises(RuntimeError, match="public key"):
            generate_keypair()


def test_encrypt_with_password():
    with patch("augint_tools.team_secrets.age.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="-----BEGIN AGE ENCRYPTED FILE-----\nencrypted\n-----END AGE ENCRYPTED FILE-----\n",
        )
        result = encrypt_with_password("secret data", "mypass")
        assert b"AGE ENCRYPTED" in result
        # Verify AGE_PASSPHRASE was set
        call_kwargs = mock_run.call_args
        assert call_kwargs.kwargs.get("env", {}).get("AGE_PASSPHRASE") == "mypass"


def test_encrypt_with_password_failure():
    with patch("augint_tools.team_secrets.age.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="bad password")
        with pytest.raises(RuntimeError, match="encrypt failed"):
            encrypt_with_password("data", "pass")


def test_decrypt_with_password():
    with patch("augint_tools.team_secrets.age.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="decrypted secret")
        result = decrypt_with_password(b"encrypted stuff", "mypass")
        assert result == "decrypted secret"


def test_decrypt_with_password_failure():
    with patch("augint_tools.team_secrets.age.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="wrong passphrase")
        with pytest.raises(RuntimeError, match="decrypt failed"):
            decrypt_with_password(b"data", "wrong")
