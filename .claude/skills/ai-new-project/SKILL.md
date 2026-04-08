---
name: ai-new-project
description: Stand up a new repository with standard quality gates, CI/CD pipeline, and configuration files.
argument-hint: "[repo-name]"
---

Create a new standardized repository: $ARGUMENTS

Tool-first project bootstrap. Use `ai-shell` for scaffolding and `ai-tools` for deterministic workflow and standards behavior.

## 1. Gather Inputs

Collect:
- repo name (use `$ARGUMENTS` when provided)
- repo type: `library` or `service` (use `workspace` only when intentionally creating a coordinator repo)
- language/ecosystem
- framework (for services)
- visibility (`private` or `public`)

## 2. Create the Repository

Use `gh` to create and clone the repo.

For `service` repos, create and push a development branch (`dev`/`develop`/`staging`) based on team convention.

## 3. Bootstrap Local Project Files

Create the baseline project skeleton for the selected ecosystem, then run package manager initialization commands.

Do not manually encode the full standards policy in this skill. Keep local scaffolding minimal and delegate standards rules to `ai-tools standardize`.

## 4. Scaffold AI Tooling

Run `ai-shell` initialization with the selected repo type and target tools:

```bash
ai-shell init --library
ai-shell init --service
ai-shell init --workspace
```

## 5. Apply Standards via `ai-tools standardize`

Run:

```bash
ai-tools standardize detect --json
ai-tools standardize audit --json
ai-tools standardize fix --write --json
ai-tools standardize verify --json
```

If the user asks for audit-only mode, skip `fix` and stop after `audit`.

## 6. Verify Workflow Surface

For normal repos, confirm:
- `ai-tools repo status --json` works
- `ai-tools repo branch prepare --json` is the branch-prep entrypoint
- `ai-tools repo submit --json` is the submit entrypoint

For workspace repos, confirm:
- `ai-tools mono status --json` works
- workspace skills map to `ai-tools mono ...`

## 7. Initial Commit and Push

Commit the scaffold with a conventional commit and push to remote.

For `service` repos, ensure branch strategy is explicit (`dev` -> `main`) before starting feature work.

## Error Handling

- `gh` not authenticated: ask user to run `gh auth login`.
- `ai-tools` missing commands: report exact missing subcommand and continue with the closest available audit/status command.
- Network failures: retry once, then report and continue with local setup where possible.
