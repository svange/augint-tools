# augint-tools

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![PyPI](https://img.shields.io/pypi/v/augint-tools.svg)](https://pypi.org/project/augint-tools/)
[![Tests](https://github.com/svange/augint-tools/actions/workflows/pipeline.yaml/badge.svg)](https://github.com/svange/augint-tools/actions)

CLI orchestration layer for AI-assisted repository workflows.

`augint-tools` provides a stable, machine-parseable command surface for humans and AI agents to coordinate development workflows. It is designed to be called directly by AI skills, replacing ad-hoc shell scripts with reliable, JSON-enabled commands.

## Features

- **AI-first design**: Every command supports `--json` output for agent parsing
- **Repo-type aware**: Understands library and service repository patterns
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

# Search/pick issues
ai-tools repo issues pick "bug"

# Create feature branch
ai-tools repo branch prepare --issue 42 --description "fix the thing"

# Run checks
ai-tools repo check run
ai-tools repo check run --preset full --fix

# Submit work (push + create PR)
ai-tools repo submit
```

## Command Reference

### Top-Level Commands

- `ai-tools init [--library|--service]` - Initialize repository metadata

### Repository Commands (`ai-tools repo`)

- `inspect` - One-call repo snapshot (kind, branch, toolchain, command plan)
- `status` - Show repository status (branch, dirty state, PRs, CI)
- `issues pick [query]` - Issue recommendation and search
- `branch prepare` - Create work branch from correct base
- `check plan` - Resolve validation plan without running
- `check run` - Execute validation plan
- `submit` - Push branch and create PR with automerge
- `ci watch` - Monitor CI run
- `ci triage` - Classify CI failures

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

## License

MIT
