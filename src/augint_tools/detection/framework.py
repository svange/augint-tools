"""Framework detection from filesystem markers."""

from pathlib import Path


def detect_framework(path: Path) -> str:
    """Detect the framework/deployment model of a repository.

    Returns: sam, cdk, terraform, vite, nextjs, or plain.
    """
    # AWS SAM
    if (path / "template.yaml").exists() or (path / "samconfig.toml").exists():
        return "sam"

    # AWS CDK
    if (path / "cdk.json").exists():
        return "cdk"

    # Terraform
    if (path / "main.tf").exists() or (path / "terraform").is_dir():
        return "terraform"

    # Next.js (check before vite since next projects may also have vite-like configs)
    if (
        (path / "next.config.js").exists()
        or (path / "next.config.mjs").exists()
        or (path / "next.config.ts").exists()
    ):
        return "nextjs"

    # Vite
    if (
        (path / "vite.config.js").exists()
        or (path / "vite.config.ts").exists()
        or (path / "vite.config.mjs").exists()
    ):
        return "vite"

    return "plain"
