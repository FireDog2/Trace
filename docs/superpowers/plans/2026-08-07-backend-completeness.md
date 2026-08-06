# Backend Completeness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete policy reading, end-to-end runtime tracing, durable skill curation, and bounded tool recovery without changing the frontend.

**Architecture:** Extend the existing SQLite repositories and TeamManager rather than replacing them. Carry one `TraceContext` across runtime boundaries, centralize retry decisions in a small recovery module, and represent curation as a restricted durable `AgentRole.CURATOR` run.

**Tech Stack:** Python 3.12, SQLite, FastAPI, sqlglot, DuckDB, pytest.

## Global Constraints

- Tests must be written and observed failing before production changes.
- Never persist DSNs, API keys, full prompts, protected messages, or unrestricted result rows.
- Runtime retries are bounded to two per failed signature and forty total tool actions.
- Curator candidates remain disabled after approval.
- Do not modify frontend files.
- Migration 13 must preserve all existing agent rows and be replay-safe.

---

### Task 1: Readable PostgreSQL policy snapshots

**Files:**
- Modify: `src/data_analysis_agent/api.py`
- Test: `tests/test_api.py`
- Test: `tests/test_data_sources.py`

**Interfaces:**
- Consumes: `DataSourceRepository.policy_snapshot(source_id)`.
- Produces: `GET /api/data-sources/{source_id}/policies -> PolicyResponse`.

- [ ] Add a repository/API test that creates mixed table and column rules, reads them back in stable order, and asserts the serialized response contains no DSN.
- [ ] Run `pytest tests/test_data_sources.py tests/test_api.py -k policy -q` and observe the GET route fail with 405/404.
- [ ] Add the GET route using the existing safe response mapper and repository not-found handling.
- [ ] Re-run the focused tests and commit `feat: expose current database policies`.

---

### Task 2: Recovery state and bounded DuckDB execution

**Files:**
- Create: `src/data_analysis_agent/recovery.py`
- Modify: `src/data_analysis_agent/settings.py`
- Modify: `src/data_analysis_agent/execution.py`
- Modify: `src/data_analysis_agent/tools.py`
- Modify: `src/data_analysis_agent/agent.py`
- Test: `tests/test_recovery.py`
- Test: `tests/test_execution.py`
- Test: `tests/test_agent.py`

**Interfaces:**
- Produces: `RecoveryPolicy.classify(result)`, `RecoveryState.record(signature, category)`, `RecoveryDecision(retry, attempt, remaining, fallback_category)`.
- Extends: `DuckDBExecutor(..., timeout_seconds)` and structured `ToolResult.diagnostics` recovery metadata.

- [ ] Write tests for retryable timeout attempts 1/2, terminal third decision, non-retryable permission/validation errors, and deterministic injected backoff.
- [ ] Write a DuckDB timeout test using a blocking connection double whose `interrupt()` must be called and whose connection must close.
- [ ] Write an AgentRunner test proving a retryable result is returned to the model, a changed call may succeed, an identical failed call stops after two attempts, and the final task has a safe category.
- [ ] Run the focused tests and observe missing recovery APIs and DuckDB timeout behavior.
- [ ] Implement the minimal recovery module, settings validation, watchdog deadline, and agent result metadata.
- [ ] Re-run focused tests and commit `feat: bound tool recovery and duckdb timeouts`.

---

### Task 3: Full correlated runtime trace

**Files:**
- Modify: `src/data_analysis_agent/db.py`
- Modify: `src/data_analysis_agent/tracing.py`
- Modify: `src/data_analysis_agent/agent.py`
- Modify: `src/data_analysis_agent/team.py`
- Modify: `src/data_analysis_agent/tools.py`
- Modify: `src/data_analysis_agent/postgres.py`
- Modify: `src/data_analysis_agent/permissions.py`
- Modify: `src/data_analysis_agent/repositories.py`
- Modify: `src/data_analysis_agent/api.py`
- Test: `tests/test_db.py`
- Test: `tests/test_trace_instrumentation.py`

**Interfaces:**
- Migration 13 adds `agent_runs.trace_run_id`, `agent_runs.parent_event_id`, and the `curator` role.
- Produces: safe trace helpers for model, teammate, tool, SQL, approval, retry, and artifact boundaries.
- Extends: `TeamManager.spawn(..., trace_context=None)` and teammate worker correlation recovery.

- [ ] Add migration replay/preservation tests using a version-12 database with existing agent rows.
- [ ] Add a golden instrumentation test asserting one ordered run contains lead, teammate, tool, SQL validation, EXPLAIN, approval, retry, completion, and artifact events with shared IDs.
- [ ] Add a redaction assertion scanning serialized trace for DSN/API-key/raw SQL sentinel values.
- [ ] Run focused tests and observe missing migration columns/correlation events.
- [ ] Implement migration 13 and pass trace context through coordination, worker, registry, PostgreSQL, approval, and artifact summaries.
- [ ] Keep trace failures secondary to the original operation and emit a safe `trace.recording_failed` task event.
- [ ] Re-run focused runtime tests and commit `feat: correlate complete agent execution traces`.

---

### Task 4: Durable restricted curator agent

**Files:**
- Modify: `src/data_analysis_agent/team_types.py`
- Modify: `src/data_analysis_agent/team.py`
- Modify: `src/data_analysis_agent/skills.py`
- Modify: `src/data_analysis_agent/api.py`
- Test: `tests/test_skill_curator.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Adds: `AgentRole.CURATOR` with an empty tool allowlist.
- Produces: `CuratorAgent.run(agent_run_id, source_run_id)` and idempotent curator lookup/spawn per source run.
- Changes: accepting a completed analysis run starts/returns the durable curator; candidate decisions stay human-only.

- [ ] Write tests proving CURATOR cannot access any tool, cannot start before acceptance, and repeated triggers create one durable agent/candidate.
- [ ] Write success, provider failure, validation failure, cancellation, and disabled-after-approval tests.
- [ ] Run focused tests and observe the missing role/runner/trigger.
- [ ] Implement the restricted curator runner using only redacted event/artifact summaries and existing static validation.
- [ ] Re-run curator/API/team tests and commit `feat: curate skills with a durable restricted agent`.

---

### Task 5: Backend acceptance and final verification

**Files:**
- Modify: `tests/test_portfolio_acceptance.py`
- Modify: `README.md`
- Modify: `docs/trace-and-skill.md`
- Modify: `docs/multi-agent-runtime.md`

**Interfaces:**
- Produces one API-key-free acceptance path from lead delegation through retryable SQL evidence, complete replay, accepted run, and durable curator candidate.

- [ ] Extend the deterministic acceptance test with shared run/agent correlation, one retry/fallback event, and one CURATOR row.
- [ ] Run `pytest tests/test_portfolio_acceptance.py -q` and observe the new assertions fail before final wiring.
- [ ] Complete only the missing integration wiring and document exact safety/retry/trace behavior plus the opt-in live PostgreSQL test.
- [ ] Run `python -m pytest -q`; expected all default tests pass with only the explicit `TEST_POSTGRES_DSN` skip.
- [ ] Run `git diff --check` and a secret scan over checked-in trace fixtures.
- [ ] Commit `test: verify complete governed agent backend`.
