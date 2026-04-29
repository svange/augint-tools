"""Team secrets repository scaffold and management."""

from __future__ import annotations

import subprocess
from pathlib import Path

from loguru import logger

from augint_tools.team_secrets.recipients import generate_sops_yaml


def init_repo(repo_path: Path, team: str) -> None:
    """Scaffold a new team secrets repository structure.

    Creates directories, placeholder files, and initial configuration.
    If the directory already exists, fills in any missing pieces.
    """
    repo_path.mkdir(parents=True, exist_ok=True)

    _write_gitignore(repo_path)
    _write_readme(repo_path, team)
    _write_sops_yaml(repo_path, team)
    _write_recipients_dir(repo_path, team)
    _write_keys_dir(repo_path)
    _write_projects_dir(repo_path)
    _write_scripts_dir(repo_path)

    # Initialize git repo if not already one
    git_dir = repo_path / ".git"
    if not git_dir.exists():
        subprocess.run(["git", "init"], cwd=repo_path, capture_output=True, check=True)
        logger.info(f"Initialized git repo at {repo_path}")


def init_project(repo_path: Path, project: str, team: str) -> None:
    """Initialize a new project directory within the team secrets repo.

    Creates the project directory with placeholder encrypted env files
    and metadata.
    """
    project_dir = repo_path / "projects" / project
    project_dir.mkdir(parents=True, exist_ok=True)

    # Create placeholder schema
    schema_path = project_dir / "schema.yaml"
    if not schema_path.exists():
        schema_path.write_text(
            "# Validation schema for encrypted env files\n"
            f"# Used by: ai-tools team-secrets {team} admin validate {project}\n"
            "keys: []\n"
        )

    # Create metadata
    metadata_path = project_dir / "metadata.yaml"
    if not metadata_path.exists():
        metadata_path.write_text(
            f'project: {project}\nrepo: ""\ndescription: ""\nenvironments:\n  - dev\n  - prod\n'
        )

    # Create empty .enc.env placeholders (will be encrypted on first edit)
    for env_name in ("dev", "prod"):
        env_file = project_dir / f"{env_name}.enc.env"
        if not env_file.exists():
            env_file.write_text(
                f"# Placeholder - encrypt with: ai-tools team-secrets {team} edit {project} "
                f"--env {env_name}\n"
            )

    logger.info(f"Initialized project '{project}' in {project_dir}")


def is_team_repo(path: Path) -> bool:
    """Check if a path looks like a valid team secrets repo."""
    return (
        path.is_dir()
        and (path / ".sops.yaml").exists()
        and (path / "recipients").is_dir()
        and (path / "projects").is_dir()
    )


def list_projects(repo_path: Path) -> list[str]:
    """List all project names in a team secrets repo."""
    projects_dir = repo_path / "projects"
    if not projects_dir.exists():
        return []
    return sorted(
        d.name for d in projects_dir.iterdir() if d.is_dir() and not d.name.startswith(".")
    )


def get_encrypted_env_path(repo_path: Path, project: str, env: str = "dev") -> Path:
    """Get the path to a specific encrypted env file."""
    return repo_path / "projects" / project / f"{env}.enc.env"


def pull_repo(repo_path: Path) -> bool:
    """Pull latest changes from the team secrets repo remote.

    Returns True if pull succeeded, False otherwise.
    """
    try:
        result = subprocess.run(
            ["git", "pull", "--no-rebase"],
            cwd=repo_path,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def commit_and_push(repo_path: Path, message: str) -> bool:
    """Stage all changes, commit, and push the team secrets repo.

    Returns True if successful.
    """
    try:
        # Stage all changes
        subprocess.run(
            ["git", "add", "-A"],
            cwd=repo_path,
            capture_output=True,
            check=True,
        )

        # Check if there are changes to commit
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        if not status.stdout.strip():
            logger.debug("No changes to commit in team repo")
            return True

        # Commit
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=repo_path,
            capture_output=True,
            check=True,
        )

        # Push
        subprocess.run(
            ["git", "push"],
            cwd=repo_path,
            capture_output=True,
            check=True,
        )
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Git operation failed: {e.stderr if e.stderr else e}")
        return False


# --- Private scaffold helpers ---


def _write_gitignore(repo_path: Path) -> None:
    path = repo_path / ".gitignore"
    if path.exists():
        return
    path.write_text(
        "# Decrypted secrets (never commit plaintext)\n"
        "*.dec.env\n"
        "*.dec.*\n"
        ".env\n"
        ".env.*\n"
        "!.env.example\n"
        "\n"
        "# Editor/OS artifacts\n"
        "*.swp\n"
        "*.swo\n"
        "*~\n"
        ".DS_Store\n"
        "Thumbs.db\n"
        "\n"
        "# Local key material (never belongs in repo)\n"
        "keys/*.txt\n"
        "!keys/*.key.enc\n"
        "!keys/README.md\n"
    )


def _write_readme(repo_path: Path, team: str) -> None:
    path = repo_path / "README.md"
    if path.exists():
        return
    path.write_text(
        f"# {team}-secrets\n"
        "\n"
        "Team shared secrets repository. Encrypted with SOPS + age.\n"
        "\n"
        "## Quick Start\n"
        "\n"
        "1. Install prerequisites: `sops` and `age`\n"
        "2. Run setup:\n"
        f"   ```bash\n"
        f"   ai-tools team-secrets {team} setup --repo .\n"
        f"   ```\n"
        "3. Edit secrets:\n"
        f"   ```bash\n"
        f"   ai-tools team-secrets {team} edit <project> --env dev\n"
        f"   ```\n"
        "4. Sync to GitHub:\n"
        f"   ```bash\n"
        f"   ai-tools team-secrets {team} sync <project>\n"
        f"   ```\n"
        "\n"
        "## Structure\n"
        "\n"
        "- `recipients/` - Age public keys for team members\n"
        "- `keys/` - Password-encrypted private keys for bootstrap\n"
        "- `projects/` - Encrypted env files per project/environment\n"
        "- `.sops.yaml` - Auto-generated SOPS configuration\n"
        "\n"
        "## Administration\n"
        "\n"
        f"- Add user: `ai-tools team-secrets {team} admin add-user <name> --pubkey <key>`\n"
        f"- Remove user: `ai-tools team-secrets {team} admin remove-user <name>`\n"
        f"- Add project: `ai-tools team-secrets {team} admin init-project <name>`\n"
        f"- Rotate keys: `ai-tools team-secrets {team} admin rotate --all`\n"
    )


def _write_sops_yaml(repo_path: Path, team: str) -> None:
    path = repo_path / ".sops.yaml"
    if path.exists():
        return
    content = generate_sops_yaml(repo_path, team)
    path.write_text(content)


def _write_recipients_dir(repo_path: Path, team: str) -> None:
    recipients_dir = repo_path / "recipients"
    recipients_dir.mkdir(exist_ok=True)

    # Team recipients file
    team_file = recipients_dir / f"team-{team}.txt"
    if not team_file.exists():
        team_file.write_text(
            f"# Team-wide age recipients for {team}\n"
            "# One public key per line, preceded by # name comment\n"
            "# Managed by: ai-tools team-secrets admin add-user/remove-user\n"
        )

    # Recipients README
    readme = recipients_dir / "README.md"
    if not readme.exists():
        readme.write_text(
            "# Recipients\n"
            "\n"
            "This directory contains age public key files.\n"
            "\n"
            "- `team-<name>.txt` - Team-wide recipients (all projects)\n"
            "- `project-<name>.txt` - Project-specific additional recipients\n"
            "\n"
            "Format: one age public key per line, optionally preceded by `# username`.\n"
            "\n"
            "These files are managed by `ai-tools team-secrets <team> admin` commands.\n"
            "Do not edit manually unless you also regenerate `.sops.yaml`.\n"
        )


def _write_keys_dir(repo_path: Path) -> None:
    keys_dir = repo_path / "keys"
    keys_dir.mkdir(exist_ok=True)

    readme = keys_dir / "README.md"
    if not readme.exists():
        readme.write_text(
            "# Keys\n"
            "\n"
            "Password-encrypted age private keys for team members.\n"
            "\n"
            "Each file `<username>.key.enc` contains a user's age private key\n"
            "encrypted with their personal password using `age -p`.\n"
            "\n"
            "## Bootstrap\n"
            "\n"
            "To decrypt and cache your key locally:\n"
            "```bash\n"
            "ai-tools team-secrets <team> setup\n"
            "```\n"
            "\n"
            "The decrypted key is cached at `~/.augint/keys/<team>/age-key.txt`\n"
            "with 600 permissions and is never committed to any repository.\n"
        )


def _write_projects_dir(repo_path: Path) -> None:
    projects_dir = repo_path / "projects"
    projects_dir.mkdir(exist_ok=True)
    gitkeep = projects_dir / ".gitkeep"
    if not gitkeep.exists():
        gitkeep.write_text("")


def _write_scripts_dir(repo_path: Path) -> None:
    scripts_dir = repo_path / "scripts"
    scripts_dir.mkdir(exist_ok=True)

    readme = scripts_dir / "README.md"
    if not readme.exists():
        readme.write_text(
            "# Scripts\n"
            "\n"
            "Optional helper scripts for team secrets management.\n"
            "\n"
            "Most operations are handled by `ai-tools team-secrets` commands.\n"
            "This directory is for any team-specific automation that falls outside\n"
            "the standard workflow.\n"
        )
