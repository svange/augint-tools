# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`augint-tools` is a CLI orchestration layer for AI-assisted repository development workflows. It replaces `augint-mono` by broadening scope from multi-repo coordination to general workflow automation.

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

```bash
ai-tools repo ...                    # Single repository workflow
```

Global output flags: `--json`, `--actionable`, `--summary`

### Command Surface

**Repo commands** (`src/augint_tools/cli/commands/repo.py`):
- `repo status` -- git state + upstream + open PR + CI + next action
- `repo branch prepare` -- create work branch from correct base
- `repo submit` -- run checks, push branch, create PR, enable automerge
- `repo ci triage` -- classify CI failures

### Core Infrastructure

- **Detection engine** (`src/augint_tools/detection/`): Shared `detect() -> RepoContext` used by all commands. Resolves language, framework, branches, toolchain, command plan, GitHub state from the filesystem and git.
- **Check system** (`src/augint_tools/checks/`): Phase enum, presets (quick/default/full/ci), plan resolution, execution runner.
- **Output model** (`src/augint_tools/output/response.py`): `CommandResponse` dataclass, `ExitCode` enum. All commands return structured responses via `emit_response()`.

### Dashboard & Health System (`src/augint_tools/dashboard/`)

The dashboard is a Textual TUI that monitors repos across orgs. Its data pipeline:

1. **GraphQL fetch** (`_gql.py`): Batched query for CI status, PRs, issues, file contents. Rulesets are NOT in this query (moved to REST for rate-limit savings).
2. **REST rulesets** (`_rulesets.py`): Fetches rulesets via REST API (separate 5,000 req/hr budget). Caches detail responses by `updated_at` -- only re-fetches when a ruleset is actually modified.
3. **Health checks** (`health/`): Pluggable check system. Each check implements the `HealthCheck` protocol and receives a `FetchContext` with pre-fetched data. No check makes its own API call.
4. **YAML compliance engine** (`health/_engine.py`, `health/_handlers.py`, `health/checks/yaml_engine.py`): Fetches `standards.yaml` from ai-cc-tools and evaluates declarative rules. See below.

### YAML Compliance Engine

**Design principle:** Rule ownership lives with the standards maintainer (ai-cc-tools repo), not here. Adding a new compliance rule is a YAML edit in ai-cc-tools, not a code change in augint-tools.

Key modules:
- `health/_engine.py` -- Core evaluation loop. Built-in check types: `file_exists`, `file_absent`, `file_content_matches`, `workflow_job_has_step`, `workflow_all_jobs_scan`, `ruleset_has_required_checks`. Template substitution (`{owner}`, `{repo_name}`, `{default_branch}`) in messages and links.
- `health/_handlers.py` -- Escape hatch for checks needing external data (AWS, HTTP). Register new handlers with `@register_handler("name")`. Three built-in: `aws_oidc_trust_policy_scope`, `http_health_probe`, `lambda_deploy_sha_match`.
- `health/checks/yaml_engine.py` -- The `YamlEngineCheck` class that bridges the engine to the health check protocol. Caches results per repo by `(commit_sha, rulesets_fingerprint)` -- unchanged repos skip re-evaluation.
- `_rulesets.py` -- REST fetcher with `updated_at` caching and a format adapter that transforms REST responses to match the GraphQL shape the engine expects.

**Adding a new built-in check type:** Add a function to `_BUILTIN_DISPATCH` in `_engine.py`.
**Adding a new handler:** Decorate a function in `_handlers.py` with `@register_handler("name")`.
**Neither needed?** Just add a YAML entry in ai-cc-tools `standards.yaml` using existing check types.

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

- `pyproject.toml` - Python packaging, dependencies, tool config
- `src/augint_tools/dashboard/_gql.py` - GraphQL query builder, response parser, `RepoSnapshot`
- `src/augint_tools/dashboard/_rulesets.py` - REST rulesets fetcher with `updated_at` caching
- `src/augint_tools/dashboard/health/_engine.py` - YAML compliance engine core
- `src/augint_tools/dashboard/health/_handlers.py` - Handler registry for external-data checks
- `src/augint_tools/dashboard/health/_registry.py` - `HealthCheck` protocol, check registration
- `src/augint_tools/dashboard/health/__init__.py` - `FetchContext` dataclass, `run_health_checks()`

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
