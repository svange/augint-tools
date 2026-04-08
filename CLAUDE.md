# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`augint-tools` is a CLI orchestration layer for AI-assisted repository and workspace development workflows. It replaces `augint-mono` by broadening scope from multi-repo coordination to general workflow automation.

**Key principle**: This tool is designed for both human operators and AI agents. Every command must provide stable `--json` output for machine parsing.

## Development Commands

```bash
# Setup
uv sync --all-extras

# Testing
uv run pytest                           # Run all tests
uv run pytest tests/unit/test_cli.py   # Run specific test file
uv run pytest -k test_name             # Run tests matching pattern
uv run pytest --cov                    # Run with coverage

# Code quality
uv run ruff check src/ tests/          # Lint
uv run ruff format src/ tests/         # Format
uv run mypy src/                       # Type check
uv run pre-commit run --all-files      # Run all pre-commit hooks

# Manual invocation (development mode)
uv run python -m augint_tools.cli --help
uv run ai-tools --help                  # Entry point script
```

## Architecture

### CLI Structure

Commands are organized in `src/augint_tools/cli/commands/`:
- `project.py` - Repo/workspace lifecycle: init, status, sync, issues, branch, submit, update
- `run.py` - Command execution: foreach, test, lint

All commands are currently scaffolded stubs that emit JSON with `implemented: false`. See `augint-tools.md` for full implementation requirements.

### Repo Classification System

Three repo types with different branching strategies:
- **library** - PyPI/npm packages, feature branches → main directly
- **service** - Services/IaC, feature branches → dev → main
- **workspace** - Coordination repo that orchestrates multiple child repos

Classification stored in `ai-shell.toml`:
```toml
[project]
repo_type = "library"
branch_strategy = "main"  # or "dev"
dev_branch = "dev"        # only when branch_strategy = "dev"
```

### Workspace Manifest (planned)

Workspace repos will define child repo orchestration in `workspace.toml`:
```toml
[[repo]]
name = "example-lib"
path = "repos/example-lib"
url = "https://github.com/org/example-lib.git"
repo_type = "library"
base_branch = "main"
pr_target_branch = "main"
install = "uv sync --all-extras"
test = "uv run pytest -m \"unit\" -v"
lint = "uv run pre-commit run --all-files"
depends_on = []  # dependency order for workspace operations
```

### JSON Output Contract

Every workflow command must support `--json` with stable schema:
```json
{
  "command": "status",
  "status": "ok",
  "scope": "workspace",
  "repos": [
    {
      "name": "repo-name",
      "present": true,
      "branch": "feat/example",
      "dirty": false,
      "ahead": 1,
      "behind": 0
    }
  ]
}
```

## Implementation Principles

1. **Stub pattern**: Unimplemented commands call `_emit_stub()` which outputs JSON with `implemented: false` and a yellow warning. This preserves the command surface while development is in progress.

2. **Safe defaults**: No destructive git operations. No silent resets. No force pushes. No rebase on default branches.

3. **AI-first design**: Commands are called by ai-shell skills, not just humans. Error messages must be specific and parseable.

4. **Workspace as a repo kind**: Workspaces are not a special architecture—they're just repos that coordinate other repos. Use the same workflow commands.

## Key Files

- `augint-tools.md` - Product spec and design doc (implementation reference)
- `ai-shell.toml` - Repo classification (consumed by ai-shell and augint-tools)
- `workspace.toml` - Workspace manifest (planned, not yet implemented)
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

## Relationship to ai-shell

**ai-shell responsibilities**: scaffold configs, persist repo kind, launch AI tools in containers

**augint-tools responsibilities**: execute workflows, inspect GitHub state, coordinate child repos, submit PRs

ai-shell skills should call augint-tools commands, not reimplement workflow logic with raw shell loops.
