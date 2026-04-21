"""Wrapper around the SOPS CLI for encrypted file operations."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def is_sops_installed() -> bool:
    """Check if sops is available on PATH and meets minimum version."""
    try:
        result = subprocess.run(
            ["sops", "--version"],
            capture_output=True,
            text=True,
            check=True,
        )
        # sops version output: "sops 3.x.y" or similar
        version_str = result.stdout.strip()
        # Extract version number - handle formats like "sops 3.9.0" or "sops version 3.9.0"
        parts = version_str.replace("sops", "").replace("version", "").strip().split(".")
        if len(parts) >= 2:
            major = int(parts[0])
            minor = int(parts[1])
            return major >= 3 and (major > 3 or minor >= 8)
    except (FileNotFoundError, subprocess.CalledProcessError, ValueError):
        pass
    return False


def decrypt_file(encrypted_path: Path, key_file: Path) -> str:
    """Decrypt a SOPS-encrypted file and return plaintext content.

    Args:
        encrypted_path: Path to the .enc.env file.
        key_file: Path to the age private key file.

    Returns:
        Decrypted plaintext content as string.
    """
    env = _sops_env(key_file)
    result = subprocess.run(
        ["sops", "--decrypt", str(encrypted_path)],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"sops decrypt failed: {result.stderr.strip()}")
    return result.stdout


def encrypt_file(plaintext_path: Path, output_path: Path, key_file: Path) -> None:
    """Encrypt a plaintext file using SOPS and write to output path.

    The .sops.yaml in the repo root determines which recipients are used
    based on path_regex rules.

    Args:
        plaintext_path: Path to the plaintext file to encrypt.
        output_path: Path where the encrypted file should be written.
        key_file: Path to the age private key file.
    """
    env = _sops_env(key_file)
    result = subprocess.run(
        [
            "sops",
            "--encrypt",
            "--input-type",
            "dotenv",
            "--output-type",
            "dotenv",
            "--output",
            str(output_path),
            str(plaintext_path),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"sops encrypt failed: {result.stderr.strip()}")


def encrypt_content(
    content: str,
    output_path: Path,
    key_file: Path,
    *,
    sops_config: Path | None = None,
) -> None:
    """Encrypt string content and write to output path.

    Uses stdin piping to avoid writing plaintext to disk.

    Args:
        content: Plaintext dotenv content to encrypt.
        output_path: Path where the encrypted file should be written.
        key_file: Path to the age private key file.
        sops_config: Optional path to .sops.yaml (for creation rules).
    """
    env = _sops_env(key_file)
    cmd = [
        "sops",
        "--encrypt",
        "--input-type",
        "dotenv",
        "--output-type",
        "dotenv",
        "--output",
        str(output_path),
        "/dev/stdin",
    ]
    if sops_config:
        cmd.extend(["--config", str(sops_config)])

    result = subprocess.run(
        cmd,
        input=content,
        capture_output=True,
        text=True,
        env=env,
        cwd=output_path.parent,
    )
    if result.returncode != 0:
        raise RuntimeError(f"sops encrypt failed: {result.stderr.strip()}")


def edit_file(encrypted_path: Path, key_file: Path) -> None:
    """Open a SOPS-encrypted file in $EDITOR for editing.

    SOPS handles decryption, editor launch, and re-encryption.
    """
    env = _sops_env(key_file)
    result = subprocess.run(
        ["sops", str(encrypted_path)],
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"sops edit exited with code {result.returncode}")


def update_keys(encrypted_path: Path, key_file: Path) -> None:
    """Run sops updatekeys to re-encrypt a file with current .sops.yaml recipients.

    This is used after adding/removing recipients to re-encrypt existing files.
    """
    env = _sops_env(key_file)
    result = subprocess.run(
        ["sops", "updatekeys", "--yes", str(encrypted_path)],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"sops updatekeys failed: {result.stderr.strip()}")


def get_file_metadata(encrypted_path: Path) -> dict:
    """Extract SOPS metadata from an encrypted file without decrypting."""
    result = subprocess.run(
        [
            "sops",
            "--output-type",
            "json",
            "--decrypt",
            "--extract",
            '["sops"]',
            str(encrypted_path),
        ],
        capture_output=True,
        text=True,
    )
    # Alternative: just parse the file directly for the sops metadata block
    # For dotenv format, sops stores metadata as comments at the end
    # Let's use a simpler approach - read the raw file and look for sops metadata
    try:
        return json.loads(result.stdout) if result.returncode == 0 else {}
    except json.JSONDecodeError:
        return {}


def _sops_env(key_file: Path) -> dict[str, str]:
    """Build environment with SOPS_AGE_KEY_FILE set."""
    import os

    env = os.environ.copy()
    env["SOPS_AGE_KEY_FILE"] = str(key_file)
    return env
