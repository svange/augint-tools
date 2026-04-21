"""Tests for team_secrets.sops module."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from augint_tools.team_secrets.sops import (
    decrypt_file,
    encrypt_content,
    is_sops_installed,
    update_keys,
)


def test_is_sops_installed_true():
    with patch("augint_tools.team_secrets.sops.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="sops 3.9.0")
        assert is_sops_installed() is True


def test_is_sops_installed_old_version():
    with patch("augint_tools.team_secrets.sops.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="sops 3.7.0")
        assert is_sops_installed() is False


def test_is_sops_installed_not_found():
    with patch("augint_tools.team_secrets.sops.subprocess.run") as mock_run:
        mock_run.side_effect = FileNotFoundError()
        assert is_sops_installed() is False


def test_is_sops_installed_version_format():
    """Handle 'sops version 3.9.0' format."""
    with patch("augint_tools.team_secrets.sops.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="sops version 3.9.0")
        assert is_sops_installed() is True


def test_decrypt_file_success():
    with patch("augint_tools.team_secrets.sops.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="KEY=value\n")
        result = decrypt_file(Path("/tmp/test.enc.env"), Path("/tmp/key.txt"))
        assert result == "KEY=value\n"
        # Check SOPS_AGE_KEY_FILE was set
        call_args = mock_run.call_args
        assert call_args.kwargs["env"]["SOPS_AGE_KEY_FILE"] == "/tmp/key.txt"


def test_decrypt_file_failure():
    with patch("augint_tools.team_secrets.sops.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="decrypt error")
        with pytest.raises(RuntimeError, match="decrypt failed"):
            decrypt_file(Path("/tmp/bad.enc.env"), Path("/tmp/key.txt"))


def test_encrypt_content_success():
    with patch("augint_tools.team_secrets.sops.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        encrypt_content("KEY=val\n", Path("/tmp/out.enc.env"), Path("/tmp/key.txt"))
        call_args = mock_run.call_args
        assert call_args.kwargs["input"] == "KEY=val\n"


def test_encrypt_content_with_config():
    with patch("augint_tools.team_secrets.sops.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        encrypt_content(
            "KEY=val\n",
            Path("/tmp/out.enc.env"),
            Path("/tmp/key.txt"),
            sops_config=Path("/tmp/.sops.yaml"),
        )
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert "--config" in cmd
        assert "/tmp/.sops.yaml" in cmd


def test_update_keys_success():
    with patch("augint_tools.team_secrets.sops.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        update_keys(Path("/tmp/test.enc.env"), Path("/tmp/key.txt"))


def test_update_keys_failure():
    with patch("augint_tools.team_secrets.sops.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="updatekeys error")
        with pytest.raises(RuntimeError, match="updatekeys failed"):
            update_keys(Path("/tmp/test.enc.env"), Path("/tmp/key.txt"))
