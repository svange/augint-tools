# Repository Guidelines

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

## Build, Test, and Development Commands

Use `uv` for local development.

- `uv sync --all-extras`: create/update the Python 3.12 environment with dev tools.
- `uv run pytest`: run the unit test suite.
- `uv run pytest --cov`: run tests with coverage reporting.
- `uv run ruff check src/ tests/`: lint Python code.
- `uv run ruff format src/ tests/`: format Python files.
- `uv run mypy src/`: run static type checks.
- `uv run pre-commit run --all-files`: execute the full local gate, including lockfile checks.
- `uv build`: build the package.

## Coding Style & Naming Conventions

Follow the existing Python style: 4-space indentation, type hints on non-CLI functions, and small focused modules. Ruff enforces import ordering and common correctness rules; line length is set to 100. Use `snake_case` for modules, functions, and test names; use `PascalCase` only for classes. Keep Click command handlers in `src/augint_tools/cli/commands/` thin and push reusable behavior into domain modules.

## Testing Guidelines

Tests use `pytest` and must follow `test_*.py` naming under `tests/`. Add or update unit tests alongside any behavior change, especially for CLI output and config parsing. Coverage is tracked from `src/`, with CLI wrappers omitted, so focus assertions on the underlying logic.

## Code review

When reviewing PRs, flag these as P0 (must fix):
- Security vulnerabilities (injection, auth bypass, secrets in code)
- Test regressions (deleted or disabled tests without replacement)

Flag these as P1 (should fix):
- Missing tests for new functionality
- Breaking changes without a `feat!:` or `fix!:` commit prefix

## Rules

- Do not edit version fields in pyproject.toml -- release automation owns these.
- Do not force push.
- Do not skip pre-commit hooks or CI checks.
- Do not add `[skip ci]` to commit messages.
- Read CLAUDE.md and README.md before starting work for additional project context.
