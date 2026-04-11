---
name: ai-repo-health
description: Comprehensive repository health check with remote-first git hygiene, branch cleanup, and code quality analysis. Use for repo maintenance, or saying 'clean up the repo'.
argument-hint: "[--remote-only] [--local-only] [--dry-run]"
---

Run a structured repository health audit: $ARGUMENTS

Primary command:
- `uv run ai-tools repo health --json $ARGUMENTS`

Report:
- hygiene findings grouped by severity
- safe cleanup actions
- prioritized next actions

Transitional fallback:
- If `uv run ai-tools repo health` is unavailable, run `uv run ai-tools repo status --actionable --json` for a limited health snapshot.
- Note that full health planning requires an updated `ai-tools` version.
