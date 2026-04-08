# augint-tools

## Purpose

`augint-tools` is a new CLI project for AI-centered developer workflows.

It replaces `augint-mono`.

The goal is broader than multi-repo orchestration. `augint-tools` should become
the home for repeatable developer workflows that are:

- AI-assisted
- repo-aware
- GitHub-aware
- safe by default
- scriptable by humans
- parseable by AI agents

This is not a repo-bundling tool. It is a workflow tool.

## Problem Statement

We currently have workflow logic split across:

- `ai-shell` skills
- repo-local helper scripts
- ad hoc shell commands
- `augint-mono`

That creates several problems:

- workflow knowledge is duplicated between docs, skills, and CLIs
- multi-repo workflows are treated as a special case instead of one workflow category
- AI agents must parse human-oriented command output
- user setup is inconsistent across library, service, and workspace repos
- repo coordination commands are coupled to the old `mono` naming

## Product Direction

`augint-tools` should be the canonical CLI for workflow automation used by:

- `ai-shell` skills
- humans at the terminal
- Codex / Claude / other AI agents

`ai-shell` should focus on:

- environment setup
- tool config scaffolding
- prompt/skill deployment
- launching AI tools in containers

`augint-tools` should focus on:

- workflow execution
- repository orchestration
- Git and GitHub actions
- machine-readable outputs

## Design Principles

1. Human and AI first
Every command must work well for both interactive humans and AI agents.

2. JSON always available
Every orchestration command should support stable `--json`.

3. Safe defaults
No destructive git behavior. No silent resets. No force pushes. No rebase on protected/default flows.

4. Repo-type aware
Libraries, services, and workspace repos have different defaults.

5. Workspace is a repo kind, not a special architecture religion
A workspace is just a coordination repo that points at child repos.

6. Skills call tools, not re-implement tools
`ai-shell` skills should orchestrate `augint-tools`, not replace it with hand-written shell logic.

## Primary Use Cases

### 1. Standard repo workflow

Examples:

- pick an issue
- prepare a branch
- run checks
- submit PR
- monitor CI

### 2. Workspace workflow

Examples:

- ensure child repos are cloned
- inspect cross-repo state
- create coordinated branches
- validate changes across selected repos
- submit related PRs
- propagate dependency updates downstream

### 3. Repo initialization workflow

Examples:

- initialize a new library repo
- initialize a new service repo
- initialize a new workspace repo
- standardize local metadata and default workflow config

## Repo Kinds

The CLI should understand these repo kinds:

- `library`
- `service`
- `workspace`

Backward compatibility can map old values:

- `iac` -> `service`

User-facing docs and commands should use only:

- `library`
- `service`
- `workspace`

## Configuration Model

### 1. Local repo config

`ai-shell.toml` remains the local repo classification file used by `ai-shell`.

Expected project section:

```toml
[project]
repo_type = "library" # or "service" or "workspace"
branch_strategy = "main" # or "dev"
dev_branch = "dev"
```

### 2. Workspace manifest

Workspace repos should also define a manifest for child repo orchestration.

Suggested filename:

`workspace.toml`

Suggested shape:

```toml
[workspace]
name = "landline-scrubber"
repos_dir = "repos"

[[repo]]
name = "ai-lls-lib"
path = "repos/ai-lls-lib"
url = "https://github.com/org/ai-lls-lib.git"
repo_type = "library"
base_branch = "main"
pr_target_branch = "main"
install = "uv sync --all-extras"
test = "uv run pytest -m \"unit\" -v"
lint = "uv run pre-commit run --all-files"

[[repo]]
name = "ai-lls-api"
path = "repos/ai-lls-api"
url = "https://github.com/org/ai-lls-api.git"
repo_type = "service"
base_branch = "dev"
pr_target_branch = "dev"
install = "uv sync --all-extras"
test = "uv run pytest -m \"unit\" -v"
lint = "uv run pre-commit run --all-files"
depends_on = ["ai-lls-lib"]
```

## Command Model

The CLI should be organized by workflow domains, not by legacy mono naming.

Top-level shape:

```bash
augint-tools init ...
augint-tools repo ...
augint-tools workspace ...
augint-tools github ...
augint-tools check ...
augint-tools submit ...
```

However, flat commands are also acceptable if they remain coherent:

```bash
augint-tools init
augint-tools status
augint-tools issues
augint-tools branch
augint-tools test
augint-tools lint
augint-tools submit
augint-tools update
```

I recommend a hybrid model:

- flat aliases for common workflows
- grouped subcommands for discoverability

Example:

```bash
augint-tools status           # alias to workspace status when in workspace repo
augint-tools workspace status # explicit form
```

## Required Commands

### `augint-tools init`

Purpose:

- initialize workflow metadata for a repo
- classify repo kind
- create or update config

Behavior:

- detect whether current repo is library/service/workspace
- if unclear, ask once
- write/update `ai-shell.toml`
- optionally verify that `ai-shell init` was run with matching kind

### `augint-tools status`

Purpose:

- summarize repo or workspace state

Repo behavior:

- current branch
- clean/dirty
- ahead/behind
- open PR
- latest CI status

Workspace behavior:

- all child repos
- missing/present
- branch per repo
- dirty state
- open PRs
- CI state
- dependency alignment summary

### `augint-tools issues`

Purpose:

- aggregate issues for the current repo or workspace

Capabilities:

- filter by label
- filter by assignee
- filter by state
- search text
- output grouped by repo

### `augint-tools branch`

Purpose:

- create or switch branches using repo-type defaults

Repo behavior:

- branch from configured base branch

Workspace behavior:

- create matching branches across selected child repos
- use per-repo `base_branch`
- never discard dirty work

### `augint-tools test`

Purpose:

- run test commands from repo config or workspace manifest

Capabilities:

- repo-aware command execution
- workspace dependency-order execution
- aggregate result summary

### `augint-tools lint`

Purpose:

- run quality checks from repo config or workspace manifest

Capabilities:

- optional `--fix`
- aggregate result summary

### `augint-tools submit`

Purpose:

- push branches and open PRs

Repo behavior:

- push current branch
- create PR against correct target

Workspace behavior:

- submit selected child repos independently
- use `pr_target_branch` per repo
- report URLs and failures

### `augint-tools update`

Purpose:

- propagate downstream dependency/version/API updates after upstream changes

Examples:

- library release requires API dependency bump
- API change requires frontend generated client update

## Output Contract

Every workflow command should support:

- normal human-readable output
- `--json`

JSON requirements:

- stable schema
- top-level `status`
- top-level `command`
- per-repo result objects
- partial failure representation without losing successes

Example:

```json
{
  "command": "status",
  "status": "ok",
  "scope": "workspace",
  "workspace": "landline-scrubber",
  "repos": [
    {
      "name": "ai-lls-lib",
      "present": true,
      "branch": "feat/issue-42-export",
      "dirty": false,
      "ahead": 1,
      "behind": 0,
      "open_prs": []
    }
  ]
}
```

## AI Integration Contract

`augint-tools` is designed to be called by AI skills directly.

That means:

- command names must be stable
- machine-readable output must be reliable
- error messages must be specific
- commands must avoid hidden side effects

Skills should become thin orchestration layers:

- `/ai-init`
- `/ai-pick-issue`
- `/ai-prepare-branch`
- `/ai-submit-work`
- `/ai-monitor-pipeline`
- `/ai-workspace-status`
- `/ai-workspace-pick`
- `/ai-workspace-branch`
- `/ai-workspace-test`
- `/ai-workspace-lint`
- `/ai-workspace-submit`
- `/ai-workspace-update`

These skills should call `augint-tools`, not reimplement workflow logic with raw shell loops.

## Relationship To ai-shell

`ai-shell` responsibilities:

- scaffold configs and skills
- persist repo kind
- launch Claude/Codex/opencode
- provide containerized environment

`augint-tools` responsibilities:

- execute repo and workspace workflows
- inspect GitHub state
- run repo-aware checks
- coordinate child repos
- submit PRs

## Migration Plan

### Phase 1

- deprecate `augint-mono`
- stop adding new features there
- update workspace docs to say `workspace`, not `workspace`
- introduce `augint-tools` spec and repo

### Phase 2

- implement workspace manifest parsing
- implement `status`, `issues`, `branch`, `test`, `lint`, `submit`, `update`
- preserve `--json` from the start

### Phase 3

- update `ai-shell` templates and skills to call `augint-tools`
- introduce workspace-named skills instead of legacy mono names

### Phase 4

- formally deprecate old skill names and old repo-type aliases

## Non-Goals

- not a general-purpose build tool
- not a package manager
- not a replacement for git
- not a replacement for GitHub CLI
- not tied only to workspace repos

## Initial MVP

The first release should include:

- `init`
- `status`
- `issues`
- `branch`
- `test`
- `lint`
- `submit`
- workspace manifest parsing
- stable `--json`

That is enough to replace the practical use of `augint-mono`.

## Naming Guidance

Use:

- `workspace`
- `child repo`
- `repo kind`
- `workflow`

Avoid:

- `workspace`
- `repo` as the default mental model
- `pointer sync`

## Summary

`augint-tools` should be the CLI home for AI-oriented engineering workflows.

It should unify:

- normal repo workflows
- workspace workflows
- GitHub-aware orchestration
- machine-readable outputs for AI skills

It replaces `augint-mono` not by rebranding it, but by broadening and clarifying
its purpose: a workflow CLI for AI-assisted engineering.
