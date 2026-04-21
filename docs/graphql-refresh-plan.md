# GraphQL refresh migration plan

## Problem

The dashboard hits GitHub's 5000 REST calls/hour limit because:

- **~10-12 REST calls per repo per refresh** (see profile below)
- Default refresh is 600s (10 min); dropping below that exhausts the budget
- User's IDEs also call GitHub Actions status APIs, sharing the same hourly budget
- Net effect: the user keeps 10-min refresh to avoid temporary blocks, and complains it's too slow

## Goal

- Drop to 60s (or lower) refresh default, with headroom
- Net REST+GraphQL calls per refresh < 10 (vs. ~240 today) for a 20-repo workspace
- No regressions in dashboard data (status, PRs, issues, CI state, teams, renovate, pipeline)

## Profile: current REST call inventory (per refresh)

Per repo:

| Source | Call | Count |
|---|---|---|
| `fetch_repo_status_with_pulls` | `repo.get_branch("dev")` | 1 |
| | `repo.get_contents("")` (root tree) | 1 |
| | `repo.get_workflow_runs(branch=default)` | 1 |
| | `run.jobs()` on failure | 0-2 |
| | `repo.get_workflow_runs(branch=dev)` if service | 0-1 |
| | `repo.get_pulls(state="open")` + paginate | 1-2 |
| | `repo.get_issues(state="open")` if open_issues > 0 | 0-2 |
| `collect_repo_teams` | `repo.get_teams()` | 1 |
| `renovate_enabled` check | `repo.get_contents(path)` x 6 config paths | 1-6 |
| `open_issues` check | `repo.get_issues(state="open")` if above threshold (duplicates the one above) | 0-2 |
| **Per-repo total** | | **6-19** |

For 20 repos, realistic avg: **~200-240 calls/refresh** → 1200-1440 calls/hour at 600s interval.

Plus upfront: `list_repos(owner)` per org (~1-2 calls) and `list_user_orgs` (1 call). Small and amortizable; not the bottleneck.

## Target: GraphQL-batched fetch

One batched GraphQL query per refresh fetches almost everything for every repo at once. GraphQL cost is point-based, not per-call; a 20-repo query costs ~20-50 points (budget = 5000/hr).

### Data we can move to GraphQL

| Field | GraphQL path |
|---|---|
| Repo private/language/default-branch | `repository(owner, name) { isPrivate primaryLanguage { name } defaultBranchRef { name } }` |
| Has dev branch | `ref(qualifiedName: "refs/heads/dev") { name }` |
| Root-tree entries (framework/IaC detection) | `object(expression: "HEAD:") { ... on Tree { entries { name } } }` |
| CI status (latest commit on default branch) | `defaultBranchRef { target { ... on Commit { statusCheckRollup { state } } } }` |
| CI status (latest commit on dev) | same via `ref(qualifiedName: "refs/heads/dev")` |
| Open PRs | `pullRequests(states: OPEN, first: 100) { totalCount nodes { number isDraft createdAt author { login } url } }` |
| Open issues (human-filtered downstream) | `issues(states: OPEN, first: 100) { totalCount nodes { number createdAt author { login } } }` |
| Renovate config content | `object(expression: "HEAD:renovate.json5") { ... on Blob { text byteSize } }` (repeat for each canonical path) |
| Pipeline workflow content (for new coverage check) | `object(expression: "HEAD:.github/workflows/pipeline.yaml") { ... on Blob { text } }` |
| Teams (org repos only) | via `organization(login) { team { repositories { ... } } }` -- reshape outside the per-repo query, or keep REST for teams (cheap: 1 call per repo, still <1% of today's traffic) |
| Rate limit visibility | `rateLimit { limit remaining resetAt cost }` in every query |

### Data that stays on REST

| Why | What |
|---|---|
| `statusCheckRollup.state` gives PASS/FAIL/PENDING but not a failing-job/step name | On failure, one REST call to get the failing run's `jobs()` for error detail. Only for repos currently failing — a small fraction. |
| Teams API is cheap per repo and org-scoped | Keep `collect_repo_teams` on REST for now. Revisit only if the dashboard is consistently limited by team calls (it isn't today). |
| Org + viewer enumeration | `list_repos_multi` and `list_user_orgs` — bootstrap only, not per-refresh hot path. |

## Scope of code changes

### New module: `src/augint_tools/dashboard/_gql.py`
- `build_workspace_query(repos: list[Repository]) -> str` — assemble batched query (chunked to 30 repos/query if workspace is larger)
- `fetch_workspace_snapshot(gh: Github, repos: list[Repository]) -> WorkspaceSnapshot` — run the query, parse into `{full_name: RepoSnapshot}`
- `RepoSnapshot` dataclass carrying every field the current refresh pulls, plus renovate config text and pipeline.yaml text
- Executes via PyGithub's `Github._Github__requester.requestJsonAndCheck("POST", "/graphql", ...)` or via a thin `httpx.Client` — prefer the PyGithub-embedded requester to reuse auth

### Modified: `_data.py`
- Keep `RepoStatus` as-is (plus add `renovate_config_path: str | None` so `renovate_enabled` can read it off the status)
- Replace `fetch_repo_status_with_pulls` body to build status from `RepoSnapshot` instead of REST calls
- Keep a `fetch_failing_run_detail(repo, run_id)` REST fallback called only when `main_status == "failure"` (or dev), to preserve the "job: step" error string

### Modified: `app.py:_do_refresh_inner`
- Replace the `ThreadPoolExecutor(max_workers=8)` per-repo loop with a single `fetch_workspace_snapshot(gh, self._repos)` call
- Then loop in-memory to build `RepoStatus` objects and invoke health checks (all off-wire now)
- Keep the failing-run detail fallback in a small ThreadPoolExecutor (maybe 4 workers) — it's only called for failing repos, which is usually ≤ 2

### Modified: health checks
- `renovate_enabled`: accept renovate config text via `FetchContext`; fall back to REST only if GraphQL returned null for every path (defensive, shouldn't happen)
- `open_issues`: **delete the duplicate** `repo.get_issues()` call. `human_open_issues` is already computed in `_data.py`. (This is a bugfix regardless of GraphQL.)
- `coverage` (new, coming from worktree 1): read pipeline.yaml text from `FetchContext`, no extra REST call

### Modified: `cmd.py` + defaults
- Change default `refresh_seconds` from 600 to 60
- Update `warn_rate_limit` to reflect the new calls_per_refresh (roughly 1-2 instead of 7)
- Keep the CLI flag; the user can always override

### Tests
- Unit test `_gql.py` with a fixture GraphQL response (one per repo shape: library, service, with-renovate, no-renovate, failing-CI, archived-about-to-be-archived)
- Regression test `fetch_repo_status_with_pulls` still returns equivalent `RepoStatus`
- Integration: tolerate a GraphQL error (network, rate-limit) by falling back to REST for that refresh cycle — this is the safety net

## Fallback strategy

| Failure mode | Handling |
|---|---|
| GraphQL HTTP error | Log, preserve `previous` state this cycle, retry next cycle |
| GraphQL partial error (one repo field errored) | Parse the other repos' fields normally; mark the errored repo with `main_status="unknown"` and log to error drawer |
| Rate-limit on GraphQL | `rateLimit.remaining` is checked in every response; log warning at <100 remaining; skip optional fields (renovate, pipeline) before skipping required fields |
| GraphQL unavailable (rare outage) | Config flag `--use-rest` forces the old code path; default is GraphQL |

## Out of scope (deliberately)

- Full async/aiohttp rewrite. GraphQL + one batched request already removes the need for massive parallelism.
- Migrating teams, org listing, or bootstrap calls — they're not the hot path.
- Changing `awsprobe` / `sysprobe` — not GitHub-related.
- Shrinking the cache schema or invalidation semantics.

## Rollout

1. Commit this plan (done).
2. **User review gate.** Do not proceed until the user approves scope.
3. Land `_gql.py` + rewrite `_data.py` + delete the obsolete REST code paths in a single
   worktree. No `--use-graphql` flag, no `--use-rest` escape hatch -- user has explicitly
   asked for the old code to be refactored out rather than parked behind a flag.
4. Flip refresh default to 60s (or 30s -- pending user answer).

## Obsolete code to delete in the same commit set

- The per-repo `ThreadPoolExecutor` loop in `app.py:_do_refresh_inner` (replaced by one
  batched GraphQL call). A tiny pool stays only for failing-run detail fetch.
- `_data.py:has_dev_branch` (REST) -- replaced by GraphQL `ref(qualifiedName:
  "refs/heads/dev")`.
- `_data.py:detect_repo_metadata` (REST `get_contents("")`) -- replaced by GraphQL
  `object(expression: "HEAD:") { entries }`.
- `_data.py:get_run_status` (REST `get_workflow_runs`) for the non-failing branches --
  replaced by GraphQL `statusCheckRollup.state`. Kept for failing runs' jobs/steps
  detail (only path that REST covers uniquely).
- `_data.py:_fetch_human_issue_summary` -- replaced by issue nodes in the GraphQL query.
- `health/checks/open_issues.py` duplicated `repo.get_issues()` call -- already available
  in `RepoStatus.human_open_issues`.
- `health/checks/renovate.py` REST probing of up to 6 config paths -- replaced by
  GraphQL `object(expression: "HEAD:<path>")` for each path in the same batched query.
- `_helpers.py:warn_rate_limit` arithmetic (assumes 7 calls/repo) -- rework around
  GraphQL points, or drop entirely if `rateLimit` is surfaced in the TUI.

## Estimated effort

- Steps 3-4: 1-2 full days of focused work
- Step 5: 30 minutes
- Biggest risk: PyGithub's GraphQL requester ergonomics and rate-limit behavior — worth a quick spike before committing to the final shape

## Open questions for user

1. OK to add `httpx` as an optional dep if PyGithub's GraphQL path is awkward? (Likely not needed, but checking.)
2. Comfortable with 60s default refresh, or want 30s? (Budget supports either easily after this change.)
3. ~~Remove `--use-rest` escape hatch after 1 week, or keep as permanent opt-out?~~
   **Resolved:** User asked for the old REST code to be refactored out entirely, not
   parked behind a flag. No escape hatch; obsolete paths deleted in the same commit set.
