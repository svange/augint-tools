# augint-tools

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![PyPI](https://img.shields.io/pypi/v/augint-tools.svg)](https://pypi.org/project/augint-tools/)
[![Tests](https://github.com/svange/augint-tools/actions/workflows/pipeline.yaml/badge.svg)](https://github.com/svange/augint-tools/actions)

CLI orchestration layer for AI-assisted repository and workspace workflows.

`augint-tools` provides a stable, machine-parseable command surface for humans and AI agents to coordinate development workflows across single repositories and multi-repo workspaces. It is designed to be called directly by AI skills, replacing ad-hoc shell scripts with reliable, JSON-enabled commands.

## Features

- **Dual-mode operation**: Commands for both single repos (`ai-tools repo`) and monorepos/workspaces (`ai-tools monorepo`)
- **AI-first design**: Every command supports `--json` output for agent parsing
- **Repo-type aware**: Understands library, service, and workspace repository patterns
- **Safe defaults**: No destructive git operations without explicit commands
- **GitHub integration**: Issue management, PR creation, CI status monitoring

## Installation

```bash
pip install augint-tools
```

Or with `uv`:

```bash
uv tool install augint-tools
```

## Quick Start

### Single Repository Workflows

```bash
# Initialize repo metadata
ai-tools init --library

# Check repository status
ai-tools repo status --json

# List issues
ai-tools repo issues "bug"

# Create feature branch
ai-tools repo branch feat/issue-42-example

# Run tests and linting
ai-tools repo test
ai-tools repo lint --fix

# Submit work (push + create PR)
ai-tools repo submit
```

### Monorepo/Workspace Workflows

```bash
# Initialize workspace
ai-tools init --workspace

# Sync all child repositories
ai-tools monorepo sync --json

# Check status across all repos
ai-tools mono status

# Create coordinated branches
ai-tools mono branch feat/multi-repo-change

# Run tests across all repos
ai-tools mono test

# Run command in all repos
ai-tools mono foreach -- git status

# Submit PRs for all modified repos
ai-tools mono submit
```

## Command Reference

### Top-Level Commands

- `ai-tools init [--library|--service|--workspace]` - Initialize repository metadata

### Repository Commands (`ai-tools repo`)

- `status` - Show repository status (branch, dirty state, PRs, CI)
- `issues [query]` - List and filter issues
- `branch <name>` - Create or switch branches using repo defaults
- `test` - Run configured test commands
- `lint [--fix]` - Run quality checks
- `submit` - Push branch and create PR

### Monorepo Commands (`ai-tools monorepo` or `ai-tools mono`)

- `status` - Status across all child repositories
- `sync` - Clone missing repos and update existing
- `issues [query]` - Aggregate issues from all repos
- `branch <name>` - Create coordinated branches
- `test` - Run tests in dependency order
- `lint [--fix]` - Quality checks across repos
- `foreach <command>` - Execute command in all repos
- `submit` - Push and create PRs for modified repos
- `update` - Propagate upstream changes downstream

## Configuration

### Repository Classification

`ai-shell.toml`:

```toml
[project]
repo_type = "library"        # or "service", "workspace"
branch_strategy = "main"     # or "dev"
dev_branch = "dev"           # when branch_strategy = "dev"
```

### Workspace Manifest

`workspace.toml` (for workspace repos):

```toml
[workspace]
name = "my-workspace"
repos_dir = "repos"

[[repo]]
name = "my-lib"
path = "repos/my-lib"
url = "https://github.com/org/my-lib.git"
repo_type = "library"
base_branch = "main"
pr_target_branch = "main"
install = "uv sync --all-extras"
test = "uv run pytest -v"
lint = "uv run pre-commit run --all-files"
depends_on = []
```

## Development

### Setup

```bash
uv sync --all-extras
```

### Running Tests

```bash
uv run pytest                    # Run all tests
uv run pytest --cov             # With coverage
uv run pytest -k test_name      # Specific test
```

### Code Quality

```bash
uv run ruff check src/ tests/   # Lint
uv run ruff format src/ tests/  # Format
uv run mypy src/               # Type check
uv run pre-commit run --all-files  # All hooks
```

## Design Principles

1. **Human and AI first** - Commands work well for both interactive use and programmatic calls
2. **JSON always available** - Every orchestration command supports stable `--json` output
3. **Safe defaults** - No destructive behavior without explicit confirmation
4. **Repo-type aware** - Different defaults for libraries, services, and workspaces
5. **Skills call tools** - AI skills orchestrate this CLI, not replace it with shell scripts

## Architecture

See [augint-tools.md](./augint-tools.md) for the complete design specification and implementation guide.

## License

MIT License - See LICENSE file for details.

## Status

**Current version: 2.0.0**

This is a complete rewrite and repurposing of the `augint-tools` package. All commands are currently scaffolded stubs that emit JSON with `implemented: false`. See [augint-tools.md](./augint-tools.md) for the implementation roadmap.
