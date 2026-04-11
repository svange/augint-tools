---
name: ai-standardize-dotfiles
description: Audit and fix project config files (.editorconfig, .gitignore, pyproject.toml tool sections). Ensures consistent development experience across repos.
argument-hint: "[--validate] [--generate] [--fix]"
---

Audit and fix project-level configuration files for this repository: $ARGUMENTS

Validates `.editorconfig`, `.gitignore` patterns, and tool configuration sections for consistency.

## Usage Examples

- `/ai-standardize-dotfiles` — Full audit with recommendations
- `/ai-standardize-dotfiles --validate` — Report issues only
- `/ai-standardize-dotfiles --generate` — Generate missing config files
- `/ai-standardize-dotfiles --fix` — Auto-fix detected issues

## 1. Detect Ecosystem

```bash
[ -f "pyproject.toml" ] && echo "python"
[ -f "package.json" ] && echo "node"
```

## 2. EditorConfig

If `.editorconfig` is missing or `--generate`, read `editorconfig-template` from `${CLAUDE_SKILL_DIR}` and write as `.editorconfig`.

If it exists, verify:
- `root = true` (prevents inheriting from parent dirs)
- `end_of_line = lf` (cross-platform consistency)
- `insert_final_newline = true` (matches pre-commit end-of-file-fixer)
- Python indent: 4 spaces
- JS/TS/YAML/JSON indent: 2 spaces
- Markdown: `trim_trailing_whitespace = false`

## 3. Gitignore

If `.gitignore` is missing or `--generate`, read `gitignore-template` from `${CLAUDE_SKILL_DIR}` and write as `.gitignore`.

Check `.gitignore` for required patterns:

### Safety (ERROR if missing)
- `.env` / `.env.*` (with `!.env.example` exception)
- `*.pem` / `*.key` / `*.crt`
- `.claude/settings.local.json`
- `.ai-shell.toml` (may contain OpenAI API keys in [codex] section)

### Build artifacts (WARNING if missing)
- Python: `*.pyc`, `__pycache__`, `dist/`, `*.egg-info`, `build/`, `.aws-sam/`
- Node: `node_modules/`, `dist/`
- Both: `.coverage`, `htmlcov/`, `.mypy_cache/`, `.ruff_cache/`, `.pytest_cache/`

### Anti-patterns (flag if present)
- `*.lock` / `uv.lock` / `package-lock.json` — lock files SHOULD be committed
- `tests/` — test code should be tracked

## 4. Python Tool Config (pyproject.toml)

### Ruff
```bash
grep -A10 '\[tool.ruff\]' pyproject.toml
```
- `line-length = 100` (not 79 or 120) — **WARNING** if different
- `select` must include at minimum `["E", "F", "I"]`. Full recommended set: `["E", "F", "I", "W", "B", "C4", "UP", "DTZ"]` — **ERROR** if `select` missing entirely

### MyPy
- `strict = true` recommended — **WARNING** if missing
- Per-module overrides acceptable (e.g., `allow_untyped_defs` for CLI)

### Coverage
- `source = ["src"]` and `omit = ["*/tests/*"]` should be configured

### Build system
- Standard: `uv_build`. If using `hatchling`/`setuptools`/`poetry-core`: **WARNING** recommending migration

## 5. Node Tool Config (package.json)

- Required scripts: `dev`, `build`, `test`, `lint`, `format`
- ESLint: flat config (`eslint.config.js`) preferred, must integrate with Prettier
- Prettier: `printWidth: 100` to match ruff's `line-length`
- TypeScript: `strict: true` recommended — **WARNING** if `false`

## Error Handling

- **No ecosystem detected**: only check .editorconfig and .gitignore
- **Multiple ecosystems**: check both Python and Node configs

## Final Output

```
=== Dotfiles Standardization Report ===
Ecosystem: Python | Action: [Generated | Validated | Fixed]

EditorConfig: [PASS] present and correct | [FAIL] MISSING
Gitignore: [PASS] .env protected | [WARN] missing .ai-shell.toml
Tool Config: [PASS] ruff configured | [WARN] mypy strict not enabled

Next steps: /ai-standardize-repo
```
