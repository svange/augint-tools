---
name: ai-pick-issue
description: Find, analyze, and recommend GitHub issues to work on. Use when looking for issues, or asking 'what should I work on next'.
argument-hint: "[issue-number or search-terms]"
---

Intelligently find or select GitHub issues: $ARGUMENTS

> Workflow automation: once an issue is selected and design alignment is reached, continue to `/ai-prepare-branch` without asking for extra confirmation.

Primary command:
- `ai-tools repo issues pick --json $ARGUMENTS`

Return:
- top issue candidates (or the requested issue)
- why each candidate is prioritized
- expected scope and dependency notes
- recommended next branch plan

Transitional fallback:
- If `ai-tools repo issues pick` is unavailable, state the tool gap and use `gh issue view` for numeric input or `gh issue list --state open --search ...` for search input.
- Never recommend closed issues as the default next task.
