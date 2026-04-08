---
name: ai-rollback
description: Rollback a bad release or revert a merged PR. Use when something broke after a merge or release. Also triggered by 'something broke', 'undo the last release', 'revert'.
argument-hint: "[PR-number or commit-sha or --dry-run]"
---

Plan and apply a rollback using the standardized repo workflow: $ARGUMENTS

Primary flow:
1. Run `ai-tools repo rollback plan --json $ARGUMENTS`.
2. Present target, impact, and warnings (including migration/infrastructure notes if present).
3. If this is a dry run, stop after the plan.
4. For non-dry-run requests, get explicit user confirmation, then run `ai-tools repo rollback apply --json $ARGUMENTS`.
5. Summarize results and continue with `/ai-monitor-pipeline`.

Transitional fallback:
- If rollback commands are unavailable, state the tool gap and ask whether to switch to a manual revert workflow.
