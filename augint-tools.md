# ai-tools Specification

## Naming

- `augint-tools` is the project/repository name.
- `ai-tools` is the CLI command that humans and agents should invoke.
- This file is the command and workflow specification for `ai-tools`.
- `augint-tools.md` can remain as product-direction background, but this file is the execution spec.

## Audit Summary

The current workflows are not perfect.

### Repo workflow

The normal single-repo flow is conceptually right:

- pick issue
- prepare branch
- develop
- submit
- monitor

But the implementation boundary is wrong. Too much logic still lives in skills:

- repo type and branch-target detection
- issue scoring and branch naming
- pre-submit check planning
- staging heuristics
- commit message generation
- PR creation and automerge setup
- CI failure triage
- promotion and rollback logic

That makes the flow less deterministic, harder to maintain, and expensive for an LLM to execute because the agent has to re-run the same shell logic from prose every time.

### Mono workflow

The mono workflow is closer to the right shape because the skills are already thin wrappers. The gaps are different:

- naming drift between `augint-tools` and `ai-tools mono`
- commands are still a little too coarse
- output filtering is not tight enough for AI use
- repo selection and dependency-aware execution can be stronger
- there is no shared compact snapshot contract for repeated status / triage / submit flows

### Standardize workflow

This is the farthest from ideal. The current standardize skills are still mostly prose plus shell snippets. The drift rules live in multiple files, detection is duplicated, and fixes are not normalized.

The standardize flow needs the same treatment as mono:

- one deterministic detection engine
- one normalized audit model
- one fix engine
- one verification pass

## Core Decisions

### 1. Keep three explicit workflow families

Use explicit subcommands for all AI-facing calls:

```bash
ai-tools repo ...
ai-tools mono ...
ai-tools standardize ...
```

Humans may still get root aliases, but skills should not rely on them.

### 2. Keep `mono` as the canonical monorepo/workspace subcommand

The existing notes, tests, and workflow language already use `ai-tools mono`. Keep that stable.

### 3. Move decision logic into the tool

Skills should orchestrate, not decide. The tool should own:

- repo detection
- branch policy
- command planning
- GitHub query batching
- CI log triage
- standard drift rules
- fixability classification
- stable machine-readable output

### 4. `ai-shell` scaffolds, `ai-tools` executes

`ai-shell` should keep owning:

- repo bootstrap
- skill installation
- context file scaffolding
- container/tool setup

`ai-tools` should own:

- workflow execution
- Git/GitHub orchestration
- repo-aware validation
- mono coordination
- standardization audit and fix

## Shared Contracts

### Configuration Inputs

`ai-tools` should resolve behavior from:

1. `ai-shell.toml`
2. `workspace.toml` for mono repos
3. repo-local conventions detected from the filesystem
4. optional `ai-tools` override sections in `ai-shell.toml`

Suggested `ai-shell.toml` expansion:

```toml
[project]
repo_type = "library" # library | service | workspace
branch_strategy = "main" # main | dev
dev_branch = "dev"

[ai_tools.repo]
update_work_branch_strategy = "rebase" # or merge
default_submit_preset = "full"

[ai_tools.commands]
quality = "uv run pre-commit run --all-files"
tests = "uv run pytest --cov=src --cov-fail-under=80 -v"
security = "uv run pip-audit"
licenses = "uv run pip-licenses --from=mixed --summary"
build = "uv build"
```

The command overrides are optional. If absent, `ai-tools` should use deterministic built-in profiles based on ecosystem and framework.

### Detection Engine

Implement one shared detection engine used by `repo`, `mono`, and `standardize`.

It should resolve at least:

- repo kind: `library`, `service`, `workspace`
- language: `python`, `typescript`, `mixed`, `unknown`
- framework: `plain`, `sam`, `cdk`, `terraform`, `vite`, `nextjs`, or detected equivalent
- default branch
- dev branch if any
- current branch
- target PR branch
- available local toolchain
- configured command plan
- GitHub availability/auth state

Expose it via:

```bash
ai-tools repo inspect
ai-tools mono inspect
ai-tools standardize detect
```

### Output Model

Every AI-facing command must support:

- human-readable compact output by default
- `--json`
- `--actionable` to suppress passing/no-op items
- `--summary` to emit only the top-level rollup and next actions

JSON contract rules:

- stable top-level keys
- `status` always present
- `command` always present
- `scope` always present
- `summary` always present
- `next_actions` always present, even if empty
- partial failures represented explicitly instead of flattening to one error string

Suggested top-level shape:

```json
{
  "command": "repo submit",
  "scope": "repo",
  "status": "ok",
  "summary": "Created PR #123 after 4 checks passed",
  "next_actions": ["monitor ci"],
  "warnings": [],
  "errors": [],
  "result": {}
}
```

### Exit Codes

Use consistent exit codes:

- `0`: success, no blockers
- `1`: operational failure
- `2`: completed with action required or drift found
- `3`: blocked by safety policy or user decision needed
- `4`: partial success

### Output Filtering

To reduce context load, commands should return only actionable detail by default.

Examples:

- `status` should not print full clean repo listings unless requested
- `check` should print failing phases first and omit passing command chatter
- `submit` should return the commit, push, PR, and unresolved blockers, not raw command output
- `standardize audit` should return findings, not raw grep output
- `mono` commands should collapse all passing repos into one rollup line unless `--verbose` is requested

## Repo Workflow

The repo workflow should become explicit, deterministic, and nearly skill-agnostic.

### Command Surface

```bash
ai-tools repo inspect
ai-tools repo status
ai-tools repo issues pick
ai-tools repo issues view
ai-tools repo branch prepare
ai-tools repo check plan
ai-tools repo check run
ai-tools repo submit
ai-tools repo ci watch
ai-tools repo ci triage
ai-tools repo promote
ai-tools repo rollback plan
ai-tools repo rollback apply
ai-tools repo health
```

### `ai-tools repo inspect`

Purpose:

- one-call snapshot for repo kind, branch policy, toolchain, and command plan

This replaces repeated detection logic in:

- `ai-status`
- `ai-prepare-branch`
- `ai-submit-work`
- `ai-promote`
- `ai-rollback`
- `ai-standardize-repo`

### `ai-tools repo status`

Purpose:

- summarize local git state, upstream relation, open PR, latest CI run, and next recommended action

Required behavior:

- batch all git and GitHub reads in one call
- compute one recommended next action
- support `--actionable`
- support `--json`

This should fully replace the raw shell dashboard inside `ai-status`.

### `ai-tools repo issues pick`

Purpose:

- deterministic issue recommendation, lookup, and search

Required behavior:

- numeric input: direct lookup
- text input: search
- empty input: recommendation mode
- include recommendation score and short reasons
- include suggested branch prefix and branch name seed
- hide closed issues from recommendation mode
- optionally include the top `N` candidates with `--limit`

This should replace the current skill-side issue scoring logic.

### `ai-tools repo branch prepare`

Purpose:

- create or switch to the correct work branch from the correct base

Required behavior:

- resolve base branch and PR target deterministically
- detect stale merged work branches
- safely handle dirty worktrees
- optionally sync dev with main when policy requires it
- generate branch name from issue metadata or description
- support exact branch names
- push the branch and set upstream

This should replace almost all of `ai-prepare-branch`.

### `ai-tools repo check plan`

Purpose:

- resolve the validation plan without running it

The plan should expand to named phases:

- `quality`
- `security`
- `licenses`
- `tests`
- `build`

Support presets:

- `quick`: quality only
- `default`: quality + tests
- `full`: quality + security + licenses + tests + build
- `ci`: mirror the configured CI policy exactly

This reduces context because the skill can inspect the resolved plan once instead of reconstructing it every turn.

### `ai-tools repo check run`

Purpose:

- execute the resolved plan with deterministic grouping and output filtering

Required behavior:

- support `--preset`
- support `--skip phase1,phase2`
- support `--fix mechanical`
- return per-phase command, duration, status, and actionable failures
- suppress raw passing tool output by default
- surface changed files when mechanical fixes were applied

Mechanical fixes allowed here:

- formatter changes
- whitespace fixes
- import sorting
- lockfile regeneration when policy explicitly allows it

Do not auto-fix business-logic test failures here.

This command is the core missing primitive for the repo flow.

### `ai-tools repo submit`

Purpose:

- turn local work into a pushed PR with the right checks, commit, and metadata

Required behavior:

- resolve submit preset
- stage tracked changes
- auto-stage known safe paths
- explicitly report unknown untracked files
- optionally fail instead of prompting on unknown files for AI mode
- run `repo check run`
- generate conventional commit message
- update work branch from target using configured strategy
- push safely
- create or update PR
- set automerge when policy says so
- optionally start CI monitoring

Suggested flags:

- `--preset quick|default|full|ci`
- `--skip phase1,phase2`
- `--unknown-files fail|prompt|ignore`
- `--update-strategy rebase|merge`
- `--monitor`
- `--draft`

This should replace the skill-side logic in `ai-submit-work`.

### `ai-tools repo ci watch`

Purpose:

- monitor a run or current-branch CI and return compact status

Required behavior:

- accept branch or run id
- wait or poll until completion
- on failure, return failed jobs first
- include log snippets, not whole logs
- include recommended next action

### `ai-tools repo ci triage`

Purpose:

- classify CI failures and optionally apply mechanical fixes

Required behavior:

- classify fixability as `mechanical`, `manual`, or `external`
- support `--fix mechanical`
- commit and push only deterministic fixes
- stop after a small configurable number of attempts
- emit structured fix attempts and remaining blockers

This should replace the heuristic-heavy logic in `ai-monitor-pipeline`.

### `ai-tools repo promote`

Purpose:

- handle the service-repo `dev -> main` promotion flow

Required behavior:

- verify the latest successful CI is for current dev HEAD
- ensure there is actually something to promote
- warn on un-PR'd commits
- create the promotion PR with merge strategy metadata
- enable automerge when allowed

This should replace the shell-heavy logic in `ai-promote`.

### `ai-tools repo rollback plan` and `apply`

Purpose:

- make rollback a planned, inspectable action instead of a manual git exercise

Required behavior:

- resolve PR or commit target
- detect migration/infrastructure risk
- emit a dry-run plan by default
- require explicit `apply` to execute
- optionally chain into CI monitoring

This should replace the manual flow in `ai-rollback`.

### `ai-tools repo health`

Purpose:

- structured repo hygiene audit and cleanup plan

This is lower priority than `check`, `submit`, and `ci`, but it should eventually absorb the procedural logic from `ai-repo-health`.

## Mono Workflow

The mono workflow already has the right direction. The goal is to make it more purposeful, tighter, and cheaper for AI use.

### Canonical Surface

```bash
ai-tools mono inspect
ai-tools mono sync
ai-tools mono status
ai-tools mono issues
ai-tools mono graph
ai-tools mono branch
ai-tools mono check
ai-tools mono test      # alias to mono check --phase tests
ai-tools mono lint      # alias to mono check --phase quality
ai-tools mono submit
ai-tools mono update
ai-tools mono foreach
```

### Required Improvements

#### 1. Use `ai-tools mono` consistently

Current workspace skills point at `augint-tools ...` while notes and tests point at `ai-tools mono ...`.

Standardize on:

```bash
ai-tools mono ...
```

`augint-tools` remains the project name, not the skill-facing command text.

#### 2. Add `mono inspect`

Purpose:

- one-call workspace snapshot

It should return:

- workspace manifest info
- repo presence
- default branch targets
- dependency graph
- blocked repos
- available selectors

#### 3. Add `mono graph`

Purpose:

- emit dependency order and affected downstream closures

This helps:

- issue planning
- check ordering
- submit planning
- downstream update planning

#### 4. Add `mono check`

Purpose:

- group common validation commands across repos

Required behavior:

- execute phases in dependency-aware order
- allow `--phase quality,tests,build`
- allow `--repos`, `--changed`, `--deps-of`, `--dependents-of`
- parallelize where safe
- collapse passing repos into a compact summary
- emit failing repos first

`mono test` and `mono lint` should remain as thin aliases.

#### 5. Improve `mono status`

Purpose:

- compact actionable workspace health

Required behavior:

- batch git and GitHub queries
- support `--actionable`
- support `--blocked-only`
- support `--dirty-only`
- support `--json`
- collapse clean repos by default

#### 6. Improve `mono branch`

Purpose:

- coordinated branch prep with deterministic repo selection

Required behavior:

- accept `--issue`, `--description`, or exact branch name
- use per-repo base branch from manifest
- skip repos that are not selected or not affected
- report blocked repos without flooding output

#### 7. Improve `mono submit`

Purpose:

- open the right PRs with minimal context load

Required behavior:

- submit only changed or selected repos
- optionally require checks to pass first
- output one structured row per repo with branch, target, PR link, and blocker
- optionally `--monitor`

#### 8. Improve `mono update`

Purpose:

- deterministic downstream propagation after upstream changes

Required behavior:

- compute dependency closure from `workspace.toml`
- support `--from repo`
- support `--plan` without writing
- support manifest-defined update commands
- emit changed repos and follow-up validation steps only

#### 9. Improve `mono foreach`

Purpose:

- keep it as the escape hatch, but make it selector-aware and compact

Required behavior:

- support selectors and dependency groups
- return per-repo status without dumping full stdout unless requested

## Standardize Workflow

This should become a real tool workflow, not a loose cluster of skills.

### Canonical Surface

```bash
ai-tools standardize detect
ai-tools standardize audit
ai-tools standardize fix
ai-tools standardize verify
```

Optional aliases are fine, but the core model should stay centered on `detect -> audit -> fix -> verify`.

### Sections

Support these sections:

- `github`
- `pipeline`
- `quality`
- `dotfiles`
- `renovate`
- `release`

Also support:

- `all`

### `ai-tools standardize detect`

Purpose:

- resolve the standardization profile once

Return:

- repo type
- language
- framework
- delivery model
- branch strategy
- expected standard profile identifier

Example profile ids:

- `python-library`
- `python-service-sam`
- `typescript-service-nextjs`
- `mixed-service`

### `ai-tools standardize audit`

Purpose:

- run all section checks through one normalized finding model

Required behavior:

- support `--section`
- support `--actionable`
- support `--json`
- never return raw grep blobs

Finding schema should include:

- `id`
- `section`
- `severity`
- `subject`
- `actual`
- `expected`
- `can_fix`
- `fix_kind`
- `source`

Suggested finding shape:

```json
{
  "id": "pipeline.job_name.code_quality",
  "section": "pipeline",
  "severity": "error",
  "subject": ".github/workflows/pipeline.yaml",
  "actual": "Pre-commit checks",
  "expected": "Code quality",
  "can_fix": true,
  "fix_kind": "patch",
  "source": "pipeline template v2"
}
```

This is the key improvement. The standard becomes data, not prose.

### `ai-tools standardize fix`

Purpose:

- apply template-backed or rule-backed fixes from the audit model

Required behavior:

- support `--section`
- support `--dry-run`
- support `--write`
- emit a change plan before writing
- distinguish local file fixes from GitHub-side fixes
- normalize GitHub-side actions even if they shell out to `ai-gh`

Fix kinds should be explicit:

- `generate`
- `patch`
- `replace`
- `external`
- `manual`

### `ai-tools standardize verify`

Purpose:

- rerun the audit after fixes and confirm the repo is aligned

This should be a first-class command so skills do not need to manually recompose the audit sequence.

### Template Ownership

`ai-tools` should own the standard templates or template references used by the fix engine.

That means:

- pipeline rules stop living only in skill prose
- renovate conventions stop living only in skill prose
- release conventions stop living only in skill prose
- dotfile and quality hook expectations stop living only in skill prose

### GitHub Provider Strategy

For the `github` section:

- prefer direct implementation when practical
- if `ai-gh` is used as a backend initially, wrap it behind normalized `ai-tools standardize` output
- do not expose raw `ai-gh` output as the primary AI contract

## ai-shell Skill Changes

After `ai-tools` grows this surface, the skills should be simplified aggressively.

### Repo skill mapping

- `ai-status` -> `ai-tools repo status --json`
- `ai-pick-issue` -> `ai-tools repo issues pick --json`
- `ai-prepare-branch` -> `ai-tools repo branch prepare --json`
- `ai-submit-work` -> `ai-tools repo submit --json`
- `ai-monitor-pipeline` -> `ai-tools repo ci watch --json` or `ci triage --json`
- `ai-promote` -> `ai-tools repo promote --json`
- `ai-rollback` -> `ai-tools repo rollback plan/apply --json`
- `ai-repo-health` -> `ai-tools repo health --json`

### Mono skill mapping

- `ai-workspace-init` -> `ai-tools mono sync` plus `mono inspect`
- `ai-workspace-sync` -> `ai-tools mono sync --json`
- `ai-workspace-status` -> `ai-tools mono status --json`
- `ai-workspace-pick` -> `ai-tools mono issues --json`
- `ai-workspace-branch` -> `ai-tools mono branch --json`
- `ai-workspace-test` -> `ai-tools mono check --phase tests --json`
- `ai-workspace-lint` -> `ai-tools mono check --phase quality --json`
- `ai-workspace-submit` -> `ai-tools mono submit --json`
- `ai-workspace-update` -> `ai-tools mono update --json`
- `ai-workspace-health` -> `ai-tools mono status --actionable --json`
- `ai-workspace-foreach` -> `ai-tools mono foreach --json`

The mono skills are already close. The main change is command normalization and better filtering.

### Standardize skill mapping

- `ai-standardize-repo` -> `ai-tools standardize audit/fix/verify`
- `ai-standardize-dotfiles` -> `ai-tools standardize audit --section dotfiles`
- `ai-standardize-precommit` -> `ai-tools standardize audit --section quality`
- `ai-standardize-pipeline` -> `ai-tools standardize audit --section pipeline`
- `ai-standardize-renovate` -> `ai-tools standardize audit --section renovate`
- `ai-standardize-release` -> `ai-tools standardize audit --section release`

Once the tool exists, these skills should become small entrypoints and reporting shells, not repositories of rules.

## Implementation Priority

### P0

- normalize naming to `ai-tools repo`, `ai-tools mono`, `ai-tools standardize`
- implement shared detection engine
- implement `repo status`
- implement `repo branch prepare`
- implement `repo check plan` and `repo check run`
- implement `repo submit`
- implement `repo ci watch` and `repo ci triage`
- implement `standardize detect`, `audit`, `fix`, `verify`
- implement `mono inspect`
- implement `mono check`
- tighten `mono status` output filtering

### P1

- implement `repo issues pick`
- implement `repo promote`
- implement `repo rollback`
- implement `mono graph`
- improve `mono update` closure planning
- improve `mono submit --monitor`

### P2

- implement `repo health`
- add more advanced provider backends for GitHub standardization
- add cached snapshots if needed for very large workspaces

## Bottom Line

The mono workflow is directionally right but needs tighter filtering, better selectors, and a grouped validation primitive.

The repo workflow is not yet tool-first enough and should get the same treatment as mono.

The standardize workflow needs the strongest rewrite: one detection engine, one audit model, one fix engine, one verify pass.

If we implement the surface in this file, the gains should be exactly the ones we want:

- more determinism
- less skill-side logic
- fewer shell calls
- smaller LLM context windows
- more repeatable repo, mono, and standardize behavior
