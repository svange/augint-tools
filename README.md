# augint-tools

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![PyPI](https://img.shields.io/pypi/v/augint-tools.svg)](https://pypi.org/project/augint-tools/)
[![CI/CD Pipeline](https://github.com/svange/augint-tools/actions/workflows/publish.yaml/badge.svg)](https://github.com/svange/augint-tools/actions)

CLI orchestration layer for AI-assisted repository workflows.

---

## Pipeline Artifacts

> Reports are published to GitHub Pages on every push to the default branch.

| Report | Link |
|--------|------|
| API Documentation | [svange.github.io/augint-tools](https://svange.github.io/augint-tools/) |
| Coverage Report | [svange.github.io/augint-tools/coverage](https://svange.github.io/augint-tools/coverage/) |
| Security Reports | [svange.github.io/augint-tools/security](https://svange.github.io/augint-tools/security/) |
| License Reports | [svange.github.io/augint-tools/compliance](https://svange.github.io/augint-tools/compliance/) |
| Test Report | [svange.github.io/augint-tools/tests](https://svange.github.io/augint-tools/tests/) |

---

## What This Does

`augint-tools` provides a stable, machine-parseable command surface for humans and AI agents to coordinate development workflows. It is designed to be called directly by AI skills, replacing ad-hoc shell scripts with reliable, JSON-enabled commands.

- **AI-first design**: Every command supports `--json` output for agent parsing
- **Repo-type aware**: Understands library and service repository patterns
- **Safe defaults**: No destructive git operations without explicit commands
- **GitHub integration**: Issue management, PR creation, CI status monitoring
- **Health dashboard**: Real-time TUI showing CI, PRs, issues, and compliance across all your repos
- **YAML compliance engine**: Declarative standards checking driven by a single `standards.yaml` -- rule ownership lives with the standards maintainer, not in this tool

---

## Getting Started

> This project uses AI-assisted development. You do not need to memorize
> git commands or CI configuration -- your AI agent handles that.

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager

### First-time setup

```bash
uv sync --all-extras
```

### Running locally

```bash
# CLI help
uv run ai-tools --help

# Check repository status
uv run ai-tools repo status --json

# Run all pre-commit hooks
uv run pre-commit run --all-files

# Run tests
uv run pytest
```

### Installation (from PyPI)

```bash
pip install augint-tools
```

Or with `uv`:

```bash
uv tool install augint-tools
```

---

## How to Contribute

> Contributions are made through AI agents (Claude Code, Copilot, etc.).
> You describe what you want changed in plain language; the agent handles
> branching, coding, testing, and submitting a pull request.

1. **Open Claude Code** (or your AI agent) in this repo.
2. **Describe the change** you want -- a bug fix, a new feature, a doc update.
3. The agent will:
   - Create a feature branch
   - Make the changes
   - Run pre-commit checks and tests
   - Open a pull request
4. **Review the PR** when the agent is done. CI runs automatically.
5. **Merge** once CI is green.

If you need to work manually, see the full [contributor guide](CONTRIBUTING.md) (if available).

---

## Architecture

### Command Surface

```bash
ai-tools repo status        # git state + upstream + open PR + CI + next action
ai-tools repo branch prepare # create work branch from correct base
ai-tools repo submit        # run checks, push branch, create PR, enable automerge
ai-tools repo ci triage     # classify CI failures
ai-tools repo check run     # execute validation plan
ai-tools repo issues pick   # issue recommendation and search
ai-tools dashboard --all    # launch the compliance TUI
```

Global output flags: `--json`, `--actionable`, `--summary`

### YAML Compliance Engine

The dashboard includes a declarative compliance engine that evaluates repos against rules defined in a `standards.yaml` file maintained in the [ai-cc-tools](https://github.com/augmenting-integrations/ai-cc-tools) repo. **Rule ownership lives with the standards maintainer, not in augint-tools.**

Adding a new compliance rule is a single YAML entry in ai-cc-tools -- no code change in augint-tools required (unless the rule needs a new handler type).

**Built-in check types:**
- `file_exists` / `file_absent` -- verify presence of config files
- `file_content_matches` -- regex with numeric/string assertions
- `workflow_job_has_step` -- verify pipeline jobs contain required steps
- `workflow_all_jobs_scan` -- detect cheat patterns (`|| true`, `continue-on-error`, `set +e`)
- `ruleset_has_required_checks` -- verify GitHub rulesets enforce expected status checks

**Handler escape hatch:** For checks that need external data (AWS API calls, HTTP probes), a `handler` type dispatches to registered Python functions. Three built-in handlers: `aws_oidc_trust_policy_scope`, `http_health_probe`, and `lambda_deploy_sha_match`.

---

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
```

### Reserved labels

| label  | glyph | treatment |
|--------|-------|-----------|
| `main` | `p`   | prod -- middle-click on card title opens this |
| `dev`  | `s`   | staging -- shift + middle-click on card title opens this |
| `pypi` | `π`   | auto-synthesized for Python libraries; manual entry overrides |

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

**Mouse** (on the title row):

- **Middle-click on the title** -> open prod URL (falls back to GitHub repo page if no `main` link)
- **Shift + middle-click on the title** -> open dev URL
- **Ctrl + left-click on the title** -> open the "Manage deployment links" modal
- **Left-click on a glyph** -> terminal-native OSC-8 link opens the URL directly

The detail drawer (press `d`) lists every link in a `deployments:` section with the host shown as visible text and the full URL as the OSC-8 target.

### Manage modal

Press `f` or middle-click on the repo name to open a modal scoped to the selected repo. The modal has dedicated fields for Production and Staging URLs at the top (Set/Clear), plus an add row for supplemental links. Existing supplementals are listed inline with Remove buttons. Every mutation writes the yaml immediately.

---

## License

MIT
