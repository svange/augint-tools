# Copilot Instructions

## Project

- **Repo:** svange/augint-tools
- **Language:** python
- **Type:** library
- **Framework:** generic

## Branch strategy

- PRs target `main`.
- Do not push directly to `main`.

## Commit messages

Use conventional commits with a scope when the change is confined to a specific module or directory.

Format: `<type>(optional-scope): <description>`

Release-triggering prefixes: `feat` (minor), `fix(deps):`, `fix`, `perf` (patch).
No-release prefixes: `chore(deps):`, `chore(deps-dev):`, `ci(deps):`, `chore`, `ci`, `docs`, `style`, `refactor`, `test`, `build`.

## Tests and linting

- Run tests: `uv run pytest`
- Run linting: `uv run pre-commit run --all-files`
- Always run tests before opening a PR. Do not skip or disable existing tests.

## Rules

- Do not edit version fields in pyproject.toml -- release automation owns these.
- Do not force push.
- Do not skip pre-commit hooks or CI checks.
- Do not add `[skip ci]` to commit messages.
- Read CLAUDE.md and README.md before starting work for additional project context.
