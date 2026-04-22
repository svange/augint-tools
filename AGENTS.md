# Repository Guidelines

## Project Structure & Module Organization
Source code lives under `src/augint_tools/`. Keep feature logic inside the existing domain packages: `cli/` for Click entrypoints and subcommands, `checks/` for validation planning and execution, `git/` and `github/` for VCS integrations, `detection/` for repo/toolchain discovery, `output/` for structured responses, and `dashboard/` for the Textual TUI and its health check system (including the YAML compliance engine -- see CLAUDE.md for architecture). Tests live in `tests/unit/`; shared sample data belongs in `tests/fixtures/`. CI assets and report styling live in `ci-resources/`.

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

## Commit & Pull Request Guidelines
Recent history follows Conventional Commits, for example `feat: ...`, `fix(security): ...`, and `chore(release): ...`. Keep commits scoped and imperative. PRs should summarize user-visible behavior, note any config or workflow impact, and link the relevant issue when one exists. Include terminal output or screenshots only when they clarify CLI behavior or CI failures.

## Security & Configuration Tips
Do not commit `.env` files; pre-commit blocks them. When changing dependencies or `pyproject.toml`, refresh and verify `uv.lock`. Prefer safe, non-destructive git operations in new commands to match the repository’s CLI contract.
