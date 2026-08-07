# Backend Completeness Design

## Goal

Close the remaining backend gaps in the agent-engineering portfolio: readable PostgreSQL policies, one correlated trace across lead/teammate/tool/SQL/approval/artifact boundaries, a durable curator agent, and bounded tool recovery with explicit fallback.

## Reference choices

The design borrows three mechanisms from lean-cc without copying its teaching-only storage:

- isolated teammate loops with role-scoped tools;
- append-only lifecycle events as the shared source of execution evidence;
- a recovery state that records which mitigations have already been attempted.

The existing SQLite repositories and `TeamManager` remain authoritative. JSONL mailboxes and untracked daemon agents are deliberately not introduced.

## Architecture

### Policy read model

`DataSourceRepository.policy_snapshot(source_id)` remains the source of truth. A new `GET /api/data-sources/{source_id}/policies` route returns the current version and every table/column rule. It never returns the protected DSN. The existing replace route remains version-producing and atomic.

### Correlated runtime trace

Every lead analysis owns one `analysis_runs.id`. `TraceContext` carries `run_id`, `task_id`, optional `agent_id`, and optional `parent_event_id`. The context flows through:

1. `AgentRunner` model and tool turns;
2. coordination tools into `TeamManager.spawn`;
3. the durable teammate row, so a worker restart can recover correlation;
4. `TeammateRunner` model/tool turns;
5. `ToolRegistry.execute` and the PostgreSQL gateway;
6. approval requests/decisions and artifact registration summaries.

Events record only bounded, redacted metadata: tool name, SQL hash, policy version, EXPLAIN cost/rows, approval ID/status, attempt, duration, safe error category, result row/byte counts, and artifact IDs. Complete prompts, tool arguments containing SQL/credentials, DSNs, protected messages, and unrestricted rows are excluded.

Trace persistence is best-effort with a visible safe failure event. A trace write failure must not replace the original model/tool/database error.

### Recovery kernel

`RecoveryPolicy` classifies errors into transient, regenerable, approval pause, and terminal categories. `RecoveryState` is scoped to an analysis run and records attempts by tool-call signature.

- transient provider/tool failures: at most two runtime retries with injectable backoff;
- query/Python/DuckDB timeout: return a structured `retryable` result to the model, including the remaining retry count and an instruction to change strategy;
- identical failed signature: at most two executions, then terminal `repeated_tool_call`;
- PostgreSQL: additionally bounded by the existing eight-query task budget;
- global fallback: forty total tool actions, after which the task ends with `tool_action_limit`;
- no retry for validation, permission denial, approval pause, policy change, result limit, or unsafe SQL.

DuckDB execution receives a real deadline. The executor owns the DuckDB connection and a watchdog calls `connection.interrupt()` when the deadline expires; the result is normalized to `query_timeout`. The watchdog is always cancelled and the connection always closes.

### Durable curator agent

Migration 13 rebuilds the `agent_runs` role constraint to add `curator` and adds `trace_run_id` plus `parent_event_id`. Existing rows and indexes are preserved.

After a human accepts a completed analysis run, the API creates one idempotent CURATOR agent run for that source run. The curator:

- receives only redacted trace summaries and artifact metadata;
- has no database, Python, report-writing, coordination, or approval tools;
- validates eligibility before the model call;
- generates a versioned candidate through `SkillStaticValidator`;
- stores the candidate disabled;
- completes with a structured handoff containing only the candidate ID/version.

Approval or rejection remains a separate human action. Approval never enables the skill and never writes into a global skill directory.

## Error handling

- Policy reads return 404 for unknown sources.
- Trace correlation missing from an old row produces a safe uncorrelated event rather than failing work.
- Curator spawn is idempotent per accepted source run; repeated accept/create requests return the existing curator/candidate state.
- Curator provider or validation failures terminate the curator row with a safe category and remain replayable.
- Timeout/retry/fallback transitions are themselves trace events.

## Verification

The implementation is complete when tests prove:

- policies round-trip through repository and GET/PUT API;
- one run trace contains ordered lead, teammate, tool, SQL validation/EXPLAIN/approval/query, retry/fallback, and artifact evidence;
- payloads remain redacted;
- CURATOR is a durable restricted role, starts only after acceptance, is idempotent, and produces a disabled candidate;
- DuckDB/Python/PostgreSQL/provider timeouts are structured and bounded;
- the model receives retryable results, repeated calls stop, and terminal fallback is persisted;
- all existing backend tests remain green; live PostgreSQL remains opt-in through `TEST_POSTGRES_DSN`.

## Non-goals

- Replacing SQLite with a distributed queue.
- Copying lean-cc JSONL mailboxes or worktree agents.
- Automatically enabling generated skills.
- Recording raw prompts, credentials, or unrestricted database results.
- Frontend redesign in this change.
