---
name: ai-submit-work
description: Run all status checks locally, fix issues, commit, push, and create an automerge PR. Use when work is ready to submit, or saying 'submit my work' or 'I'm done'.
argument-hint: "[--no-tests] [--no-security]"
---

Submit the current branch using the standardized repo workflow: $ARGUMENTS

Primary command:
- `ai-tools repo submit --json $ARGUMENTS`

Report:
- checks executed and failing phases
- commit/push result
- PR link and target
- unresolved blockers requiring manual action

If submit succeeds and a PR is open, continue with `/ai-monitor-pipeline`.

Transitional fallback:
- If `ai-tools repo submit` is unavailable, try `ai-tools repo check run --json` to provide pre-submit validation context.
- If repo commands are missing, state the tool gap and recommend upgrading `ai-tools` instead of rebuilding full submit logic in skill prose.
