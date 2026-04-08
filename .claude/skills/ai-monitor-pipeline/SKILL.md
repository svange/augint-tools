---
name: ai-monitor-pipeline
description: Monitor CI pipeline after push, diagnose failures, auto-fix and re-push. Use after submitting work, or asking 'check the build' or 'how's the pipeline'.
argument-hint: "[run-id or branch-name]"
---

Monitor CI and triage failures with the repo workflow tool: $ARGUMENTS

Primary commands:
- `ai-tools repo ci watch --json $ARGUMENTS`
- if failures remain actionable: `ai-tools repo ci triage --json $ARGUMENTS`

Report:
- run status and failed jobs
- whether failures are auto-fixable or manual
- concrete next step

Transitional fallback:
- If repo CI commands are unavailable, use `gh run watch` and `gh run view --log-failed` for a minimal diagnosis.
- Recommend upgrading `ai-tools` for deterministic CI triage behavior.
