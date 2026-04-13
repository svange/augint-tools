# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`augint-tools` is a CLI orchestration layer for AI-assisted repository and workspace development workflows. It replaces `augint-mono` by broadening scope from multi-repo coordination to general workflow automation.

**Key principle**: This tool is designed for both human operators and AI agents. Every command must provide stable `--json` output for machine parsing.

## Critical Rules

- **No rebase on main**: NEVER use `git pull --rebase` or `git rebase` on the default branch. Use merge commits only.
- **No manual versioning**: NEVER manually edit version numbers. Semantic Release manages versions via conventional commits.
- **No lock file edits**: NEVER directly write text into lock files (uv.lock, package-lock.json). Always use package manager commands (`uv lock`, `uv add`) to regenerate them. Always stage and include lock file changes in the same commit.
- **No .env commits**: NEVER commit .env files. Use .env.example for templates.
- **No force push to main**: NEVER use `git push --force` on main or the default branch.
- **No manual release tags**: NEVER create release tags by hand. Semantic Release creates them from conventional commits.

## Development Commands

```bash
# Setup
uv sync --all-extras

# Testing
uv run pytest                                # Run all tests
uv run pytest tests/unit/test_cli.py        # Run specific test file
uv run pytest -k test_name                  # Run tests matching pattern
uv run pytest --cov=src --cov-fail-under=80 # Tests with coverage threshold

# Code quality
uv run ruff check src/ tests/          # Lint
uv run ruff format src/ tests/         # Format
uv run mypy src/                       # Type check
uv run pre-commit run --all-files      # Run all pre-commit hooks

# Manual invocation (development mode)
uv run python -m augint_tools.cli --help
uv run ai-tools --help                  # Entry point script
```

## Conventions

- **Commits**: Conventional commits required. `fix:` = patch, `feat:` = minor, `feat!:` / `BREAKING CHANGE` = major. Choose prefixes intentionally -- they trigger releases.
- **Branches**: `{type}/issue-N-description` where type is one of: feat, fix, docs, refactor, test, chore, ci, build, style, revert, perf.
- **PRs**: Target the default development branch. Enable automerge.
- **Pre-commit**: Run `uv run pre-commit run --all-files` explicitly before committing (no automatic git hooks -- they break across Windows/WSL). If checks fail, fix the issue and create a NEW commit (do not amend).
- **Tests**: Write tests for all new functionality. Bug fixes require regression tests.

## Development Workflow

**IMPORTANT**: Always follow this sequence. Do NOT skip to step 3 without completing step 2 first.

1. **Pick an issue**: `/ai-pick-issue` -- find or get assigned work
2. **Prepare branch**: `/ai-prepare-branch` -- REQUIRED before any code changes. Creates a fresh branch from the latest base (main or dev), syncs upstream, sets up remote tracking. Never start coding on an existing branch from a previous task.
3. **Develop**: Write code with tests, following project conventions
4. **Submit**: `/ai-submit-work` -- runs all checks locally, commits, pushes, creates automerge PR
5. **Monitor**: `/ai-monitor-pipeline` -- watches CI, diagnoses failures, auto-fixes and re-pushes

## Architecture

### CLI Structure

Two workflow families under `ai-tools`:

```bash
ai-tools repo ...                    # Single repository workflow
ai-tools workspace ...               # Workspace orchestration
```

Global output flags: `--json`, `--actionable`, `--summary`

> This repo is a **library**. Use `repo` commands. Do not use `workspace` commands -- those are for workspace repos only.

### Command Surface

**Repo commands** (`src/augint_tools/cli/commands/repo.py`):
- `repo status` -- git state + upstream + open PR + CI + next action
- `repo branch prepare` -- create work branch from correct base
- `repo submit` -- run checks, push branch, create PR, enable automerge
- `repo ci triage` -- classify CI failures

**Workspace commands** (`src/augint_tools/cli/commands/workspace.py`):
- `workspace sync` -- clone/pull child repos
- `workspace status` -- workspace health (--actionable, --blocked-only, --dirty-only)
- `workspace branch` -- coordinated branch prep (--issue, --description, --name)
- `workspace check` -- grouped validation across repos (--phase, --repos, --preset)
- `workspace submit` -- open PRs for changed repos
- `workspace foreach` -- arbitrary command across repos

### Workspace Environment Variables

Two optional environment variables support proxied/containerized environments (e.g., Claude Code Web):

- **`WORKSPACE_REPOS_DIR`** -- Override directory where child repos are located. When set, `get_repo_path()` resolves repos as `$WORKSPACE_REPOS_DIR/<repo_name>` instead of using the path from workspace.yaml. Affects all workspace commands.
- **`GIT_CLONE_URL_TEMPLATE`** -- URL template for git clone operations. Supports `{slug}` (owner/repo), `{org}`, and `{repo}` placeholders. Example: `http://local_proxy@127.0.0.1:9999/git/{org}/{repo}`. Only affects `workspace sync` clone operations.

When neither env var is set, `workspace sync` also auto-detects proxy environments by inspecting the workspace root's origin remote URL. If it matches the Claude Code Web proxy pattern (`http://local_proxy@127.0.0.1:PORT/git/*`), clone URLs are automatically rewritten to use the same proxy.

Additionally, `get_repo_path()` auto-detects sibling repo layouts: if the configured path has no `.git/` but a sibling path (`../repo_name`) does, it uses the sibling. This handles Claude Code Web's flat cloning layout.

### Core Infrastructure

- **Detection engine** (`src/augint_tools/detection/`): Shared `detect() -> RepoContext` used by all commands. Resolves language, framework, branches, toolchain, command plan, GitHub state from the filesystem and git.
- **Check system** (`src/augint_tools/checks/`): Phase enum, presets (quick/default/full/ci), plan resolution, execution runner.
- **Output model** (`src/augint_tools/output/response.py`): `CommandResponse` dataclass, `ExitCode` enum. All commands return structured responses via `emit_response()`.

### Output Contract

Every command returns a `CommandResponse` with this JSON shape:
```json
{
  "command": "repo submit",
  "scope": "repo",
  "status": "ok",
  "summary": "Created PR #123 after 4 checks passed",
  "next_actions": ["monitor ci"],
  "warnings": [],
  "errors": [],
  "result": {}
}
```

Exit codes: 0=success, 1=failure, 2=action-required, 3=blocked, 4=partial

## Implementation Principles

1. **Safe defaults**: No destructive git operations. No silent resets. No force pushes. No rebase on default branches.

2. **AI-first design**: Commands are called by AI agents, not just humans. Error messages must be specific and parseable.

3. **Detection once**: Commands call `detect()` once at the top and pass the `RepoContext` down. No scattered detection logic.

4. **No config files**: Detection is purely filesystem- and git-based. No `ai-shell.toml` or similar config files. Toolchain, language, and branch targets are inferred from the repo itself.

## Key Files

- `workspace.yaml` - Workspace manifest for workspace repos
- `pyproject.toml` - Python packaging, dependencies, tool config

## Testing Strategy

- Unit tests in `tests/unit/`
- Coverage excludes CLI command files (see `tool.coverage.run.omit` in pyproject.toml)
- Pre-commit hooks enforce: formatting, linting, type checking, uv.lock consistency, no committed .env files

## Release Process

Uses semantic-release with conventional commits:
- Tag format: `augint-tools-v{version}`
- Version stored in: `pyproject.toml:project.version` and `src/augint_tools/__init__.py:__version__`
- Build command: `uv lock && uv build`
- No git hooks skipped (`no_git_verify = false`)
