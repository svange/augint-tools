"""Supplemental verification checks that augment ai-shell standardize --verify.

These checks detect issues that ai-shell does not currently catch:

- T10-3: pip-licenses missing from dev deps when compliance gate exists
- T10-4: Quality gate thresholds that may be stale
- T12-1: Multiple Renovate config files (precedence conflicts)
- T12-2: delete_branch_on_merge enabled on repos with long-lived branches
- T12-3: Renovate config on default branch differs from working branch
- T12-4: CI skip keywords in workflow files
- T12-5: Wrong tokens used for check-runs API in promote workflows
- T13-2: forbid-env-commit hook without .env.example exclusion
- T13-8: no-commit-to-branch hook without pipeline SKIP
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

# Finding dict shape matches ai-shell: {section, status, message, diff, is_clean}
Finding = dict[str, Any]

# --------------------------------------------------------------------------- #
# Renovate config file names in Renovate's precedence order (highest first).
# --------------------------------------------------------------------------- #
RENOVATE_CONFIG_FILES = [
    "renovate.json",
    "renovate.json5",
    ".renovaterc",
    ".renovaterc.json",
]

_CANONICAL_RENOVATE = "renovate.json5"

# --------------------------------------------------------------------------- #
# CI skip keywords that suppress GitHub Actions.
# --------------------------------------------------------------------------- #
_CI_SKIP_PATTERN = re.compile(
    r"\[(skip ci|ci skip|no ci|skip actions|actions skip)\]",
    re.IGNORECASE,
)

# --------------------------------------------------------------------------- #
# Quality gate threshold patterns.
# --------------------------------------------------------------------------- #
_MAX_WARNINGS_PATTERN = re.compile(r"--max-warnings\s+(\d+)")


# =========================================================================== #
# Public entry point                                                          #
# =========================================================================== #


def run_supplemental_checks(path: Path, *, area: str | None = None) -> list[Finding]:
    """Run supplemental checks for *area* (or all areas when ``None``).

    Returns a list of finding dicts in the same shape as ai-shell findings.
    """
    findings: list[Finding] = []

    if area is None or area == "renovate":
        findings.extend(check_renovate_dual_config(path))
        findings.extend(check_renovate_default_branch(path))

    if area is None or area == "pipeline":
        findings.extend(check_pip_licenses_dep(path))
        findings.extend(check_quality_gate_thresholds(path))
        findings.extend(check_ci_skip_keywords(path))
        findings.extend(check_workflow_token_usage(path))

    if area is None or area == "precommit":
        findings.extend(check_no_commit_to_branch_skip(path))
        findings.extend(check_forbid_env_commit_exclusion(path))

    if area is None:
        findings.extend(check_delete_branch_on_merge(path))

    return findings


# =========================================================================== #
# T13-2: JSON5 formatting noise filter                                        #
# =========================================================================== #

# Pattern for unquoted JSON5 keys: $schema:, extends:, baseBranchPatterns:
# Group 1 captures the prefix (start-of-string or delimiter), group 2 the key.
_JSON5_UNQUOTED_KEY = re.compile(r"(^|[{,\s])(\$?[a-zA-Z_][\w$.-]*)\s*:")


def _normalize_json5_text(text: str) -> str:
    """Normalize JSON5 text to canonical JSON for semantic comparison.

    Handles unquoted keys, single quotes, trailing commas, and whitespace.
    Comments are preserved because they carry semantic meaning in Renovate
    configs (e.g. the repo-type header).  This is intentionally *not* a
    full parser -- it covers the patterns that appear in Renovate configs.
    """
    s = text
    # Trailing commas before } or ].
    s = re.sub(r",(\s*[}\]])", r"\1", s)
    # Single quotes -> double quotes.
    s = s.replace("'", '"')
    # Quote unquoted keys.
    s = _JSON5_UNQUOTED_KEY.sub(r'\1"\2":', s)
    # Collapse whitespace so multi-line vs single-line arrays match.
    s = re.sub(r"\s+", " ", s).strip()
    # Normalize whitespace inside brackets/braces.
    s = re.sub(r"\[\s+", "[", s)
    s = re.sub(r"\s+]", "]", s)
    s = re.sub(r"\{\s+", "{", s)
    s = re.sub(r"\s+}", "}", s)
    return s


def filter_renovate_formatting_noise(findings: list[Finding]) -> None:
    """T13-2: Detect and filter JSON5 formatting noise in renovate diffs.

    Modifies *findings* in place. For renovate DRIFT findings whose diff is
    entirely formatting noise (JSON5 vs JSON style), the status is downgraded
    to PASS. For mixed diffs (semantic + formatting changes), only the
    formatting-only hunks are stripped.
    """
    for finding in findings:
        section = finding.get("section", "")
        if section != "renovate":
            continue
        if finding.get("status") != "DRIFT":
            continue
        diff = finding.get("diff")
        if not diff:
            continue

        # Parse the unified diff into hunks.
        hunks: list[list[str]] = []
        current: list[str] = []
        header_lines: list[str] = []

        for line in diff.splitlines():
            if line.startswith("---") or line.startswith("+++"):
                header_lines.append(line)
            elif line.startswith("@@"):
                if current:
                    hunks.append(current)
                current = [line]
            else:
                current.append(line)
        if current:
            hunks.append(current)

        semantic_hunks: list[list[str]] = []
        had_formatting = False

        for hunk in hunks:
            minus = [ln[1:] for ln in hunk if ln.startswith("-") and not ln.startswith("---")]
            plus = [ln[1:] for ln in hunk if ln.startswith("+") and not ln.startswith("+++")]

            minus_norm = _normalize_json5_text("\n".join(minus))
            plus_norm = _normalize_json5_text("\n".join(plus))

            if minus_norm == plus_norm:
                had_formatting = True
            else:
                semantic_hunks.append(hunk)

        if not had_formatting:
            continue

        if not semantic_hunks:
            # Entire diff is formatting noise -- downgrade to PASS.
            finding["status"] = "PASS"
            finding["is_clean"] = True
            original_msg = finding.get("message", "")
            finding["message"] = (
                f"{original_msg} (formatting differences only -- semantically clean)"
                if original_msg
                else "formatting differences only -- semantically clean"
            )
            finding["diff"] = None
        else:
            # Mixed: keep DRIFT but strip formatting hunks from the diff.
            rebuilt = header_lines[:]
            for h in semantic_hunks:
                rebuilt.extend(h)
            finding["diff"] = "\n".join(rebuilt)
            msg = finding.get("message", "")
            finding["message"] = (
                f"{msg} (formatting-only hunks filtered from diff)"
                if msg
                else "formatting-only hunks filtered from diff"
            )


# =========================================================================== #
# Individual checks                                                           #
# =========================================================================== #


# --- T12-1: Dual Renovate config ------------------------------------------ #


def check_renovate_dual_config(path: Path) -> list[Finding]:
    """Detect multiple Renovate config files that cause precedence conflicts."""
    found: list[str] = []
    for name in RENOVATE_CONFIG_FILES:
        if (path / name).exists():
            found.append(name)

    pkg_json = path / "package.json"
    if pkg_json.exists():
        try:
            data = json.loads(pkg_json.read_text())
            if isinstance(data, dict) and "renovate" in data:
                found.append("package.json[renovate]")
        except (json.JSONDecodeError, OSError):
            pass

    if len(found) <= 1:
        return []

    highest = found[0]
    return [
        {
            "section": "renovate_config",
            "status": "DRIFT",
            "message": (
                f"Multiple Renovate configs: {', '.join(found)}. "
                f"Renovate uses highest-precedence file ({highest})"
                + (
                    f", which shadows the canonical {_CANONICAL_RENOVATE}."
                    if highest != _CANONICAL_RENOVATE
                    else "."
                )
            ),
            "diff": None,
            "is_clean": False,
        }
    ]


# --- T12-3: Renovate config on default branch ----------------------------- #


def check_renovate_default_branch(path: Path) -> list[Finding]:
    """Check that Renovate config on the default branch matches the working copy."""
    local_config: Path | None = None
    for name in RENOVATE_CONFIG_FILES:
        candidate = path / name
        if candidate.exists():
            local_config = candidate
            break

    if local_config is None:
        return []

    default_branch = _detect_default_branch(path)
    if default_branch is None:
        return []

    current_branch = _current_branch(path)
    if current_branch is None or current_branch == default_branch:
        return []

    config_name = local_config.name
    default_content = _git_show(path, f"origin/{default_branch}", config_name)

    if default_content is None:
        return [
            {
                "section": "renovate_default_branch",
                "status": "DRIFT",
                "message": (
                    f"{config_name} exists locally but not on {default_branch}. "
                    f"Renovate reads config from the default branch."
                ),
                "diff": None,
                "is_clean": False,
            }
        ]

    try:
        local_content = local_config.read_text()
    except OSError:
        return []

    if local_content.strip() != default_content.strip():
        return [
            {
                "section": "renovate_default_branch",
                "status": "DRIFT",
                "message": (
                    f"{config_name} on {default_branch} differs from working copy. "
                    f"Renovate reads from {default_branch} -- merge or update."
                ),
                "diff": None,
                "is_clean": False,
            }
        ]

    return []


# --- T12-4: CI skip keywords ---------------------------------------------- #


def check_ci_skip_keywords(path: Path) -> list[Finding]:
    """Scan workflow files for CI skip keywords in run/body blocks."""
    workflows_dir = path / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return []

    findings: list[Finding] = []
    for yml in sorted(workflows_dir.glob("*.yml")):
        try:
            content = yml.read_text()
        except OSError:
            continue

        for i, line in enumerate(content.splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith("#") or stripped.startswith("if:"):
                continue
            match = _CI_SKIP_PATTERN.search(line)
            if match:
                findings.append(
                    {
                        "section": "pipeline_ci_skip",
                        "status": "FAIL",
                        "message": (
                            f"{yml.name}:{i}: CI skip keyword '{match.group()}' "
                            f"found in workflow text. This suppresses CI on "
                            f"commits/PRs that include this text."
                        ),
                        "diff": None,
                        "is_clean": False,
                    }
                )

    return findings


# --- T12-5: Workflow token usage ------------------------------------------- #


def check_workflow_token_usage(path: Path) -> list[Finding]:
    """Check that promote workflows use correct tokens for check-runs API."""
    workflows_dir = path / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return []

    findings: list[Finding] = []
    for yml in sorted(workflows_dir.glob("*.yml")):
        name_lower = yml.stem.lower()
        if "promote" not in name_lower:
            continue

        try:
            content = yml.read_text()
        except OSError:
            continue

        has_check_api = bool(re.search(r"check-runs|check-suites", content))
        if not has_check_api:
            continue

        # T12-5 rule 1: check-runs calls must use github.token, not PAT.
        lines = content.splitlines()
        for i, line in enumerate(lines, 1):
            if re.search(r"check-runs|check-suites", line) and "secrets.GH_TOKEN" in line:
                findings.append(
                    {
                        "section": "pipeline_token_usage",
                        "status": "DRIFT",
                        "message": (
                            f"{yml.name}:{i}: check-runs/check-suites API should "
                            f"use github.token, not secrets.GH_TOKEN. "
                            f"Fine-grained PATs return 403 on the checks API."
                        ),
                        "diff": None,
                        "is_clean": False,
                    }
                )

        # Also check for GH_TOKEN in env/with blocks near check-runs usage.
        # Scan for blocks that set GH_TOKEN then reference check-runs.
        _check_token_in_step_blocks(yml.name, lines, findings)

        # T12-5 rule 2: permissions block should include checks: read.
        if "checks:" not in content:
            findings.append(
                {
                    "section": "pipeline_token_usage",
                    "status": "DRIFT",
                    "message": (
                        f"{yml.name}: uses check-runs/check-suites API but "
                        f"workflow permissions block is missing 'checks: read'."
                    ),
                    "diff": None,
                    "is_clean": False,
                }
            )

    return findings


def _check_token_in_step_blocks(filename: str, lines: list[str], findings: list[Finding]) -> None:
    """Detect steps where GH_TOKEN env is set and check-runs API is called."""
    in_step = False
    step_has_gh_token = False
    step_has_check_api = False
    step_start = 0

    for i, line in enumerate(lines, 1):
        stripped = line.lstrip()
        # A new step starts at "- name:", "- uses:", or "- run:"
        if (
            stripped.startswith("- name:")
            or stripped.startswith("- uses:")
            or (stripped.startswith("- run:") and not in_step)
        ):
            # Emit finding for the previous step if both conditions met.
            if in_step and step_has_gh_token and step_has_check_api:
                findings.append(
                    {
                        "section": "pipeline_token_usage",
                        "status": "DRIFT",
                        "message": (
                            f"{filename}:{step_start}: step uses secrets.GH_TOKEN "
                            f"and calls check-runs/check-suites API. "
                            f"Use github.token for checks API access."
                        ),
                        "diff": None,
                        "is_clean": False,
                    }
                )
            in_step = True
            step_has_gh_token = False
            step_has_check_api = False
            step_start = i

        if in_step:
            if "secrets.GH_TOKEN" in line:
                step_has_gh_token = True
            if re.search(r"check-runs|check-suites", line):
                step_has_check_api = True

    # Final step.
    if in_step and step_has_gh_token and step_has_check_api:
        findings.append(
            {
                "section": "pipeline_token_usage",
                "status": "DRIFT",
                "message": (
                    f"{filename}:{step_start}: step uses secrets.GH_TOKEN "
                    f"and calls check-runs/check-suites API. "
                    f"Use github.token for checks API access."
                ),
                "diff": None,
                "is_clean": False,
            }
        )


# --- T10-3: pip-licenses dependency --------------------------------------- #


def check_pip_licenses_dep(path: Path) -> list[Finding]:
    """Check that pip-licenses is in dev deps when a compliance gate uses it."""
    workflows_dir = path / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return []

    has_pip_licenses_job = False
    for yml in workflows_dir.glob("*.yml"):
        try:
            if "pip-licenses" in yml.read_text():
                has_pip_licenses_job = True
                break
        except OSError:
            continue

    if not has_pip_licenses_job:
        return []

    pyproject = path / "pyproject.toml"
    if not pyproject.exists():
        return []

    try:
        content = pyproject.read_text()
    except OSError:
        return []

    if "pip-licenses" in content:
        return []

    return [
        {
            "section": "pipeline_deps",
            "status": "DRIFT",
            "message": (
                "Compliance gate references pip-licenses but it is not in "
                "pyproject.toml dev dependencies. Add with: "
                "uv add --group dev 'pip-licenses>=5.0.0'"
            ),
            "diff": None,
            "is_clean": False,
        }
    ]


# --- T10-4: Quality gate thresholds --------------------------------------- #


def check_quality_gate_thresholds(path: Path) -> list[Finding]:
    """Detect quality gate thresholds that should be validated before push."""
    workflows_dir = path / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return []

    # Only relevant for Node repos.
    if not (path / "package.json").exists():
        return []

    findings: list[Finding] = []
    for yml in sorted(workflows_dir.glob("*.yml")):
        try:
            content = yml.read_text()
        except OSError:
            continue

        for match in _MAX_WARNINGS_PATTERN.finditer(content):
            findings.append(
                {
                    "section": "pipeline_thresholds",
                    "status": "DRIFT",
                    "message": (
                        f"{yml.name}: lint gate has --max-warnings {match.group(1)} "
                        f"cap. Verify this threshold is current before pushing "
                        f"pipeline changes (run the lint command locally)."
                    ),
                    "diff": None,
                    "is_clean": False,
                }
            )

    return findings


# --- T12-2: delete_branch_on_merge ---------------------------------------- #


def check_delete_branch_on_merge(path: Path) -> list[Finding]:
    """Check that delete_branch_on_merge is disabled on repos with long-lived branches."""
    if not _has_long_lived_branches(path):
        return []

    slug = _get_repo_slug(path)
    if slug is None:
        return []

    try:
        proc = subprocess.run(
            ["gh", "api", f"repos/{slug}", "--jq", ".delete_branch_on_merge"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    if proc.returncode != 0:
        return []

    value = proc.stdout.strip().lower()
    if value != "true":
        return []

    return [
        {
            "section": "repo_settings",
            "status": "DRIFT",
            "message": (
                "delete_branch_on_merge is enabled but this repo has long-lived "
                "branches (dev). GitHub auto-delete bypasses branch rulesets and "
                "will delete dev on every merge to main. Fix with: "
                f"gh api repos/{slug} -X PATCH -f delete_branch_on_merge=false"
            ),
            "diff": None,
            "is_clean": False,
        }
    ]


# --- T13-8: no-commit-to-branch without pipeline SKIP -------------------- #


def check_no_commit_to_branch_skip(path: Path) -> list[Finding]:
    """Detect no-commit-to-branch in pre-commit config without SKIP in pipeline.

    The hook blocks commits on main/dev, which is fine for local dev but breaks
    post-merge CI when the pipeline runs ``pre-commit run --all-files``.
    """
    pre_commit_cfg = path / ".pre-commit-config.yaml"
    if not pre_commit_cfg.exists():
        return []

    try:
        content = pre_commit_cfg.read_text()
    except OSError:
        return []

    if "no-commit-to-branch" not in content:
        return []

    # Check if pipeline.yaml already sets SKIP to handle this.
    pipeline = path / ".github" / "workflows" / "pipeline.yaml"
    if pipeline.exists():
        try:
            pipeline_text = pipeline.read_text()
            if "no-commit-to-branch" in pipeline_text and "SKIP" in pipeline_text:
                return []
        except OSError:
            pass

    return [
        {
            "section": "precommit_ci_compat",
            "status": "DRIFT",
            "message": (
                ".pre-commit-config.yaml has no-commit-to-branch hook but "
                "pipeline.yaml does not set SKIP for it. Post-merge CI on "
                "main/dev will fail. Either remove the hook (rulesets already "
                "protect branches) or add SKIP: no-commit-to-branch to the "
                "pipeline quality step."
            ),
            "diff": None,
            "is_clean": False,
        }
    ]


# --- T13-2: forbid-env-commit without .env.example exclusion ------------- #


def check_forbid_env_commit_exclusion(path: Path) -> list[Finding]:
    """Detect forbid-env-commit hook that would catch .env.example files."""
    pre_commit_cfg = path / ".pre-commit-config.yaml"
    if not pre_commit_cfg.exists():
        return []

    try:
        content = pre_commit_cfg.read_text()
    except OSError:
        return []

    if "forbid-env-commit" not in content:
        return []

    # Check if any .env.example or .env.sample files exist.
    has_env_template = any((path / name).exists() for name in (".env.example", ".env.sample"))
    if not has_env_template:
        return []

    # Check if the hook already has an exclude for example/sample.
    # The exclude value may use a literal dot or an escaped dot (\\.).
    if re.search(r"exclude:.*\\?\.?\(?(example|sample)", content):
        return []

    return [
        {
            "section": "precommit_env_exclusion",
            "status": "DRIFT",
            "message": (
                ".pre-commit-config.yaml has forbid-env-commit hook but no "
                "exclude for .env.example/.env.sample. This will block "
                "committing configuration templates. Add: "
                "exclude: '\\.(example|sample)$'"
            ),
            "diff": None,
            "is_clean": False,
        }
    ]


# =========================================================================== #
# Helpers                                                                     #
# =========================================================================== #


def _detect_default_branch(path: Path) -> str | None:
    """Return the default branch name, or None if it cannot be determined."""
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "symbolic-ref", "refs/remotes/origin/HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip().replace("refs/remotes/origin/", "")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _current_branch(path: Path) -> str | None:
    """Return the current branch name."""
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _git_show(path: Path, ref: str, file: str) -> str | None:
    """Read a file from a git ref. Returns None if not found."""
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "show", f"{ref}:{file}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _has_long_lived_branches(path: Path) -> bool:
    """Detect if the repo has long-lived branches (dev, staging, etc.)."""
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "branch", "-a"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            return False

        branches = result.stdout
        for name in ("dev", "staging", "develop"):
            if f"/{name}\n" in branches or f"/{name}" in branches.split():
                return True
            # Check local branches too.
            for line in branches.splitlines():
                if line.strip().lstrip("* ") == name:
                    return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return False


def _get_repo_slug(path: Path) -> str | None:
    """Extract owner/repo from the git remote URL."""
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            return None
        url = result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    url = url.rstrip("/").removesuffix(".git")
    if "github.com" not in url:
        return None

    if url.startswith("git@"):
        parts = url.split(":")
        if len(parts) == 2:
            return parts[1]
    else:
        parts = url.split("/")
        if len(parts) >= 2:
            return f"{parts[-2]}/{parts[-1]}"

    return None
