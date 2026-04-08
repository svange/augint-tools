---
name: ai-status
description: Show current workspace status, open PRs, pipeline state, and suggested next step. Also triggered by 'where am I', 'what's going on', 'status'.
argument-hint: ""
---

Show current repo status and suggest the next action: $ARGUMENTS

Primary command:
- `ai-tools repo status --json $ARGUMENTS`

Summarize:
- branch and local git state (dirty / ahead / behind)
- open PR and latest CI state
- one recommended next action

Transitional fallback:
- If `ai-tools repo status` is unavailable, state that this `ai-tools` version is missing the command.
- Run a minimal snapshot with `git branch --show-current`, `git status --short`, and (if available) `gh pr list` / `gh run list` for the current branch.
- Recommend upgrading `ai-tools`.
