---
name: ai-standardize-repo
description: Audit and fix repository standards (pipeline, rulesets, pre-commit, renovate, release, dotfiles) against universal quality gates.
argument-hint: "[--validate|--fix] [github|pipeline|quality|dotfiles|renovate|release|all]"
---

Audit and fix repository standards: $ARGUMENTS

Use the tool-first workflow. Standards logic should live in `ai-tools standardize`, not in skill prose.

Primary flow:
1. Detect profile:
   - `ai-tools standardize detect --json`
2. Audit:
   - `ai-tools standardize audit --json [--section <section>] [--actionable]`
3. If `--validate` is present, stop after audit.
4. If `--fix` is present (or user approves fixes), run:
   - `ai-tools standardize fix --write --json [--section <section>]`
5. Verify final state:
   - `ai-tools standardize verify --json [--section <section>]`

Sections:
- `github`
- `pipeline`
- `quality`
- `dotfiles`
- `renovate`
- `release`
- `all`

Report:
- findings by section and severity
- fixes applied by kind (generate/patch/replace/external/manual)
- remaining manual actions

If these commands are unavailable, state the tool gap and stop instead of recreating standardization rules here.
