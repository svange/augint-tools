---
name: ai-init
description: Initialize ai-shell for a new repo or workspace and choose the correct repo type and tool setup.
argument-hint: "[--library | --service | --workspace] [--codex | --claude | --opencode | --all]"
---

Set up AI tooling for this repository: $ARGUMENTS

Use this when starting a new repo or when `ai-shell` has not been initialized yet.
The goal is to choose the right repo type, install only the relevant skills, and avoid context litter.

## 1. Determine Repo Kind

Choose one:
- `library` - package/published artifact, usually `main` branching
- `service` - deployable app/API/web/IaC repo, usually `dev` -> `main`
- `workspace` - coordination repo for multiple child repos

If the user does not specify a kind, ask once and persist it via `ai-shell.toml`.

## 2. Determine Tool Scope

Choose one:
- `--claude`
- `--codex`
- `--opencode`
- `--all`

Prefer initializing only the tool(s) the user actually uses.

## 3. Run the Right Command

Examples:

```bash
ai-shell init --library
ai-shell init --service
ai-shell init --workspace

ai-shell claude --init --service
ai-shell codex --init --library
ai-shell opencode --init --workspace
```

If the repo already has `ai-shell.toml`, prefer `--update` over `--init`.

## 4. Verify Result

Confirm that:
- `ai-shell.toml` has the expected `[project]` metadata
- only the relevant skills were installed
- workspace repos get `ai-workspace-*` skills and use `ai-tools workspace ...`
- normal repos use `ai-tools repo ...`
- standardization flows use `ai-tools standardize ...`
- normal repos do not get workspace-only skills

## 5. Explain the Natural Workflow

For `library` / `service` repos:
- `/ai-pick-issue`
- `/ai-prepare-branch`
- develop
- `/ai-submit-work`
- `/ai-monitor-pipeline`
- `/ai-standardize-repo` for standards alignment (`ai-tools standardize ...`)

For `workspace` repos:
- `/ai-workspace-sync`
- `/ai-workspace-status`
- `/ai-workspace-pick`
- `/ai-workspace-branch`
- develop across child repos
- `/ai-workspace-test`
- `/ai-workspace-lint`
- `/ai-workspace-submit`
- `/ai-workspace-update`

## Error Handling

- If repo kind is unclear, stop and ask once.
- If wrong skills are already installed, use `--update` or `--reset`.
- If the repo was initialized with an older alias like `iac`, explain the current equivalent (`service`).
