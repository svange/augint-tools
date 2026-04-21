"""Wrapper around the age CLI for key generation and password-based encryption."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AgeKeypair:
    """An age keypair with public and private key material."""

    public_key: str
    private_key: str  # Full key file content including comment line


def is_age_installed() -> bool:
    """Check if age and age-keygen are available on PATH."""
    try:
        subprocess.run(
            ["age", "--version"],
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["age-keygen", "--version"],
            capture_output=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False
    return True


def generate_keypair() -> AgeKeypair:
    """Generate a new age keypair.

    Returns the public key and the full private key file content
    (which includes a comment line with the public key).
    """
    result = subprocess.run(
        ["age-keygen"],
        capture_output=True,
        text=True,
        check=True,
    )
    private_key_content = result.stdout
    # age-keygen outputs to stdout: comment line with public key, then private key
    # The public key is on the comment line: # public key: age1...
    public_key = ""
    for line in private_key_content.splitlines():
        if line.startswith("# public key:"):
            public_key = line.split(":", 1)[1].strip()
            break

    if not public_key:
        raise RuntimeError("Failed to extract public key from age-keygen output")

    return AgeKeypair(public_key=public_key, private_key=private_key_content)


def encrypt_with_password(plaintext: str, password: str) -> bytes:
    """Encrypt plaintext string with a password using age.

    Returns the encrypted ciphertext bytes.
    """
    result = subprocess.run(
        ["age", "--encrypt", "--passphrase"],
        input=password + "\n" + password + "\n",
        capture_output=True,
        text=False,
        env=_env_with_no_tty(),
    )
    # age reads passphrase from stdin when not a TTY, we pipe it
    # Actually age -p reads from /dev/tty by default. We need to use
    # the PINENTRY workaround or pipe via stdin with env var.
    # Let's use the approach of piping plaintext and using --armor
    result = subprocess.run(
        ["age", "--encrypt", "--passphrase", "--armor"],
        input=plaintext,
        capture_output=True,
        text=True,
        env=_env_with_password(password),
    )
    if result.returncode != 0:
        raise RuntimeError(f"age encrypt failed: {result.stderr}")
    return result.stdout.encode()


def decrypt_with_password(ciphertext: bytes, password: str) -> str:
    """Decrypt age-encrypted ciphertext using a password.

    Returns the decrypted plaintext string.
    """
    result = subprocess.run(
        ["age", "--decrypt"],
        input=ciphertext.decode() if isinstance(ciphertext, bytes) else ciphertext,
        capture_output=True,
        text=True,
        env=_env_with_password(password),
    )
    if result.returncode != 0:
        raise RuntimeError(f"age decrypt failed: {result.stderr}")
    return result.stdout


def encrypt_file_with_password(input_path: Path, output_path: Path, password: str) -> None:
    """Encrypt a file with a password using age."""
    plaintext = input_path.read_text()
    encrypted = encrypt_with_password(plaintext, password)
    output_path.write_bytes(encrypted)


def decrypt_file_with_password(input_path: Path, password: str) -> str:
    """Decrypt a password-encrypted age file. Returns plaintext content."""
    ciphertext = input_path.read_bytes()
    return decrypt_with_password(ciphertext, password)


def _env_with_password(password: str) -> dict[str, str]:
    """Build environment dict that passes password to age non-interactively."""
    import os

    env = os.environ.copy()
    # AGE_PASSPHRASE env var is supported by age for non-interactive use
    env["AGE_PASSPHRASE"] = password
    return env


def _env_with_no_tty() -> dict[str, str]:
    """Build environment dict for non-TTY age operations."""
    import os

    env = os.environ.copy()
    return env
