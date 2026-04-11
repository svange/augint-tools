---
name: ai-prepare-branch
description: Create a feature branch from the correct base (dev or main), sync release bumps, and set up remote tracking. Use when starting work on an issue or saying 'start working on'.
argument-hint: "[issue-number, description, or branch-name]"
---

Prepare a work branch with the configured repo policy: $ARGUMENTS

Primary command:
- `uv run ai-tools repo branch prepare --json $ARGUMENTS`

Report:
- selected base branch and PR target branch
- prepared branch name
- repos/conditions blocked by local state
- next command to run

Transitional fallback:
- If `uv run ai-tools repo branch prepare` is unavailable, state the tool gap first.
- Run only a minimal safe fallback (`git fetch --all --prune`, detect base branch, create/switch branch).
- Keep fallback logic small; do not recreate the old shell-heavy branching algorithm in this skill.
