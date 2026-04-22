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
- **Health dashboard**: Real-time TUI showing CI, PRs, issues, and compliance across all your repos
- **YAML compliance engine**: Declarative standards checking driven by a single `standards.yaml` -- rule ownership lives with the standards maintainer, not in this tool

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

## Dashboard & Compliance Engine

The dashboard (`ai-tools dashboard`) is a Textual TUI that monitors all your repos in real time: CI status, open PRs, issue counts, and compliance findings on one screen. It refreshes via batched GraphQL queries with REST-based ruleset fetching on a separate rate-limit pool.

### YAML Compliance Engine

The dashboard includes a declarative compliance engine that evaluates repos against rules defined in a `standards.yaml` file maintained in the [ai-cc-tools](https://github.com/augmenting-integrations/ai-cc-tools) repo. This is the key design decision: **rule ownership lives with the standards maintainer, not in augint-tools.**

Adding a new compliance rule is a single YAML entry in ai-cc-tools -- no code change in augint-tools required (unless the rule needs a new handler type).

**Built-in check types:**
- `file_exists` / `file_absent` -- verify presence of config files
- `file_content_matches` -- regex with numeric/string assertions (e.g., coverage threshold >= 80)
- `workflow_job_has_step` -- verify pipeline jobs contain required steps
- `workflow_all_jobs_scan` -- detect cheat patterns (`|| true`, `continue-on-error`, `set +e`)
- `ruleset_has_required_checks` -- verify GitHub rulesets enforce expected status checks

**Handler escape hatch:** For checks that need external data (AWS API calls, HTTP probes), a `handler` type dispatches to registered Python functions. Three built-in handlers ship today: `aws_oidc_trust_policy_scope`, `http_health_probe`, and `lambda_deploy_sha_match`.

**Caching:** The engine caches results per repo by `(commit_sha, rulesets_fingerprint)`. Unchanged repos skip re-evaluation entirely. Rulesets are fetched via REST with `updated_at`-based caching so config drift is detected in real time without re-fetching detail data every cycle.

```bash
# Launch the dashboard
ai-tools dashboard --all

# Override the standards URL (e.g., test a branch's rules before merge)
ai-tools dashboard --all --standards-yaml-url "https://api.github.com/repos/org/repo/contents/standards.yaml?ref=my-branch"
```

## Dashboard Deployment Links

Each repo card can surface clickable shortcuts to its live deployment URLs (plus the repo's PyPI page for Python libraries). Links come from a user-global yaml file so the same config works from any terminal or WSL shell.

### Yaml file

Path: `~/.augint-tools/deployments.yaml` (resolves to `%USERPROFILE%\.augint-tools\deployments.yaml` on Windows).

Schema: a map of `owner/repo` slugs to a flat list of `{label, url}` entries.

```yaml
augmentingintegrations/aillc-web:
  - { label: dev,  url: "https://www.org.aillc.link/" }
  - { label: main, url: "https://www.augmentingintegrations.com/" }
augmentingintegrations/ai-lls-api:
  - { label: dev,  url: "https://lls-api.lls.aillc.link" }
  - { label: main, url: "https://lls-api.landlinescrubber.link" }
augmentingintegrations/woxom-sales-dashboard:
  - { label: dashboard,         url: "https://dashboard.woxom.aillc.link/" }
  - { label: jacksonhealthcare, url: "https://jacksonhealthcare.woxom.aillc.link/" }
```

### Reserved labels

| label  | glyph | treatment |
|--------|-------|-----------|
| `main` | `p`   | prod -- middle-click on card title opens this |
| `dev`  | `s`   | staging -- shift + middle-click on card title opens this |
| `pypi` | `π`   | auto-synthesized for Python libraries (see below); manual entry overrides the auto guess |

Any other label is free-form; the card glyph is the first alphanumeric character of the label (lowercased).

Deployment glyphs are rendered right-aligned on the CI status line of each card (e.g. `dev PASS  main PASS       s p`). Each glyph is an OSC-8 hyperlink clickable in supported terminals.

### Auto-PyPI

If a repo has the `py` tag, is not a service (`looks_like_service=False`), and is not an org repo (`is_org=False`), the card and drawer get an automatic `https://pypi.org/project/<repo-name>/` link rendered in a dim style. If the repo's PyPI name differs from its GitHub name, add a manual `pypi` entry in the yaml -- manual entries always win.

### Interaction model

**Keyboard shortcuts** (work on the selected repo, one-hand cluster):

| Key | Action |
|-----|--------|
| `z` | Open prod/main deployment URL |
| `x` | Open dev/staging deployment URL |
| `c` | Open 1st supplemental link (after main/dev) |
| `v` | Open 2nd supplemental link |
| `b` | Open 3rd supplemental link |
| `f` | Open the "Manage deployment links" modal |

**Mouse** (on the title row; depends on terminal modifier support):

- **Middle-click on the title** -> open prod URL (falls back to the GitHub repo page if no `main` link is configured).
- **Shift + middle-click on the title** -> open dev URL, or toast "no url configured for dev" if none.
- **Ctrl + left-click on the title** -> open the "Manage deployment links" modal for that repo.
- **Left-click on a glyph** (`s` / `p` / `π` / first-letter) -> terminal-native OSC-8 link opens the URL directly.

Note: mouse modifier support (shift+click, ctrl+click) varies by terminal. Windows Terminal may intercept ctrl+click for OSC-8 link handling. The keyboard shortcuts (`p`, `P`, `u`) are the reliable primary interface.

The detail drawer (press `d`) lists every link in a `deployments:` section with the host shown as visible text and the full URL as the OSC-8 target.

### Manage modal

Press `f` or middle-click on the repo name to open a modal scoped to the selected repo. The modal has dedicated fields for Production and Staging URLs at the top (Set/Clear), plus an add row for supplemental links. Existing supplementals are listed inline with Remove buttons (AWS Security Groups style). Every mutation writes the yaml immediately. Auto-PyPI entries are not listed (they aren't stored in the yaml); add a manual `pypi` row to override the guess.

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
