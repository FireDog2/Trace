# Trace Replay and Skill Curation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist a redacted, correlated trace for every lead/teammate/model/tool/approval action, replay it without re-execution, and turn an accepted successful run into a disabled candidate skill.

**Architecture:** Keep existing `task_events` as the lightweight SSE feed and add dedicated `analysis_runs` plus `trace_events` for durable observability. A `TraceRecorder` accepts typed events from lead, teammate, tool, SQL, and approval boundaries; `WorkflowCurator` consumes only accepted successful traces and writes a versioned candidate after static safety checks.

**Tech Stack:** Python 3.12, SQLite, `contextvars`, FastAPI, existing model provider abstraction, React/TypeScript, pytest/Vitest.

## Global Constraints

- Trace replay is read-only and never invokes a model, tool, or database.
- Never persist credentials, API keys, complete restricted query results, full unbounded prompts, or raw sensitive exception details in trace payloads.
- Every trace event must carry run/task identifiers, optional agent and parent identifiers, type, status, timing, attempt, and redacted JSON payload.
- Only successful, explicitly accepted runs with valid evidence and no unresolved errors may produce a candidate skill.
- Candidate skills are versioned, human-reviewed, and disabled by default.
- Preserve existing SSE task-event behavior and endpoint compatibility.

---

### Task 1: Migrations 5 and 6 plus trace repositories

**Files:**
- Modify: `src/data_analysis_agent/db.py`
- Create: `src/data_analysis_agent/tracing.py`
- Create: `tests/test_tracing.py`
- Modify: `tests/test_db.py`

**Interfaces:**
- Produces schema versions 5 and 6.
- Produces `AnalysisRun`, `TraceEvent`, `TraceRepository.start_run`, `finish_run`, `accept_run`, `append`, `list_runs`, and `events_for_run`.
- Produces `SkillCandidate` and `SkillCandidateRepository` in a later task using migration 6.

- [ ] **Step 1: Write migration and repository tests**

```python
def test_trace_migrations_are_versioned(db_connection) -> None:
    names = {
        row[0] for row in db_connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {"analysis_runs", "trace_events", "skill_candidates"} <= names
    assert db_connection.execute(
        "SELECT MAX(version) FROM schema_migrations"
    ).fetchone() == (6,)


def test_trace_repository_preserves_parent_child_order(db_connection, task) -> None:
    repository = TraceRepository(db_connection)
    run = repository.start_run(task.id)
    parent = repository.append(
        run.id, task.id, "agent.started", "running", {"role": "lead"}
    )
    child = repository.append(
        run.id,
        task.id,
        "tool.started",
        "running",
        {"tool": "query_database"},
        parent_event_id=parent.id,
        agent_id="agent-sql",
    )
    assert [item.id for item in repository.events_for_run(run.id)] == [parent.id, child.id]
```

- [ ] **Step 2: Run tests and verify the expected failure**

Run: `python -m pytest tests/test_tracing.py tests/test_db.py -v`

Expected: FAIL because migrations 5/6 and `tracing.py` do not exist.

- [ ] **Step 3: Add migration 5**

```python
MIGRATION_5_SQL = """
CREATE TABLE analysis_runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    status TEXT NOT NULL,
    accepted INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    error_category TEXT
);

CREATE TABLE trace_events (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES analysis_runs(id),
    task_id TEXT NOT NULL REFERENCES tasks(id),
    agent_id TEXT,
    parent_event_id TEXT REFERENCES trace_events(id),
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    duration_ms INTEGER,
    attempt INTEGER NOT NULL DEFAULT 1,
    error_category TEXT,
    artifact_ids_json TEXT NOT NULL DEFAULT '[]',
    UNIQUE(run_id, sequence)
);

CREATE INDEX idx_trace_events_run_sequence ON trace_events(run_id, sequence);
"""
```

- [ ] **Step 4: Add migration 6**

```python
MIGRATION_6_SQL = """
CREATE TABLE skill_candidates (
    id TEXT PRIMARY KEY,
    source_run_id TEXT NOT NULL REFERENCES analysis_runs(id),
    name TEXT NOT NULL,
    version INTEGER NOT NULL,
    markdown TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('candidate', 'approved', 'rejected')),
    enabled INTEGER NOT NULL DEFAULT 0,
    generated_by_model TEXT NOT NULL,
    reviewed_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(name, version)
);
"""
```

Apply migrations in numeric order and insert their version rows transactionally.

- [ ] **Step 5: Implement trace repository with atomic sequence allocation**

`append()` must use `BEGIN IMMEDIATE`, select `COALESCE(MAX(sequence), 0) + 1` for the run, insert one event, and commit. Validate that the run belongs to the supplied task. `finish_run()` only permits `completed`, `failed`, or `cancelled`; `accept_run()` only accepts a completed run.

```python
def append(
    self,
    run_id: str,
    task_id: str,
    event_type: str,
    status: str,
    payload: dict[str, Any],
    *,
    agent_id: str | None = None,
    parent_event_id: str | None = None,
    attempt: int = 1,
    error_category: str | None = None,
    artifact_ids: tuple[str, ...] = (),
) -> TraceEvent:
    with self._transaction():
        sequence = self._next_sequence(run_id, task_id)
        return self._insert_event(
            run_id, task_id, sequence, event_type, status, redact_payload(payload),
            agent_id, parent_event_id, attempt, error_category, artifact_ids,
        )
```

- [ ] **Step 6: Run repository and migration tests**

Run: `python -m pytest tests/test_tracing.py tests/test_db.py -v`

Expected: all selected tests PASS.

- [ ] **Step 7: Commit trace persistence**

```powershell
git add src/data_analysis_agent/db.py src/data_analysis_agent/tracing.py tests/test_tracing.py tests/test_db.py
git commit -m "feat: persist correlated analysis traces"
```

---

### Task 2: Redaction and runtime instrumentation

**Files:**
- Modify: `src/data_analysis_agent/tracing.py`
- Modify: `src/data_analysis_agent/agent.py`
- Modify: `src/data_analysis_agent/team.py`
- Modify: `src/data_analysis_agent/tools.py`
- Modify: `src/data_analysis_agent/postgres.py`
- Modify: `src/data_analysis_agent/api.py`
- Create: `tests/test_trace_instrumentation.py`

**Interfaces:**
- Consumes: `TraceRepository` and runtime boundaries from prior plans.
- Produces `TraceContext(run_id, task_id, agent_id, parent_event_id)`, `TraceRecorder.event`, `TraceRecorder.span`, and `redact_payload`.
- Adds optional trace context to `ToolContext` without changing existing callers.

- [ ] **Step 1: Write redaction and correlation tests**

```python
def test_redactor_removes_nested_secrets_and_truncates_large_values() -> None:
    payload = {
        "dsn": "postgresql://analyst:secret@localhost/db",
        "headers": {"Authorization": "Bearer api-secret"},
        "preview": "x" * 20_000,
    }
    redacted = redact_payload(payload)
    serialized = json.dumps(redacted)
    assert "secret" not in serialized
    assert "[REDACTED]" in serialized
    assert len(redacted["preview"]) <= 4_200


def test_golden_run_correlates_lead_teammate_tool_and_sql_events(traced_services, task) -> None:
    completed = traced_services.agent().run(task.id)
    run = traced_services.traces.list_runs(task.id)[0]
    events = traced_services.traces.events_for_run(run.id)
    assert completed.status is TaskStatus.COMPLETED
    assert {event.event_type for event in events} >= {
        "agent.started",
        "model.completed",
        "teammate.created",
        "tool.started",
        "sql.validated",
        "sql.explained",
        "tool.completed",
        "agent.completed",
    }
    assert all(event.run_id == run.id for event in events)
```

- [ ] **Step 2: Run instrumentation tests and verify the expected failure**

Run: `python -m pytest tests/test_trace_instrumentation.py -v`

Expected: FAIL because redaction and instrumentation are absent.

- [ ] **Step 3: Implement deterministic redaction**

Redact keys matching this case-insensitive set:

```python
SECRET_KEYS = frozenset({
    "api_key", "authorization", "cookie", "dsn", "password",
    "protected_api_key", "protected_dsn", "secret", "token",
})
```

Replace sensitive values with `[REDACTED]`, recursively handle dict/list/tuple, limit strings to 4,096 characters, lists to 100 items, and nesting to 8 levels. Do not attempt reversible masking.

- [ ] **Step 4: Implement recorder and spans**

```python
@dataclass(frozen=True, slots=True)
class TraceContext:
    run_id: str
    task_id: str
    agent_id: str | None = None
    parent_event_id: str | None = None


@contextmanager
def span(self, context: TraceContext, event_type: str, payload: dict, *, attempt: int = 1):
    started = monotonic()
    event = self.event(context, event_type, "running", payload, attempt=attempt)
    try:
        yield replace(context, parent_event_id=event.id)
    except Exception as exc:
        self.repository.complete_event(
            event.id,
            status="failed",
            duration_ms=int((monotonic() - started) * 1000),
            error_category=type(exc).__name__,
        )
        raise
    else:
        self.repository.complete_event(
            event.id,
            status="completed",
            duration_ms=int((monotonic() - started) * 1000),
        )
```

- [ ] **Step 5: Instrument runtime boundaries**

Allow `AgentRunner.run(task_id, trace_context=None)` to create and own an analysis run when no context is supplied, so direct service and acceptance-test calls are traced. `_run_task`/`_continue_task` may create the run first and pass its `TraceContext`; in both paths exactly one component owns terminal `finish_run`. Record model call summaries without full prompts. Pass the same run ID and teammate agent ID into teammate runners. Record SQL validation, policy decision, explain summary, approval, query completion, and artifact IDs inside the gateway.

```python
def run(self, task_id: str, trace_context: TraceContext | None = None) -> Task:
    owns_run = trace_context is None
    if owns_run:
        run = self._traces.start_run(task_id)
        trace_context = TraceContext(run.id, task_id)
    try:
        task = self._run_loop(task_id, trace_context)
    except Exception:
        if owns_run:
            self._traces.finish_run(trace_context.run_id, "failed", "agent_error")
        raise
    if owns_run:
        self._traces.finish_run(trace_context.run_id, _trace_status(task.status))
    return task
```

Keep existing `task_events` writes for SSE. Trace failures must not hide the original tool/model failure; if trace persistence fails, append a safe `trace.recording_failed` task event and fail the analysis run with `trace_failure`.

- [ ] **Step 6: Run focused and existing runtime tests**

Run: `python -m pytest tests/test_trace_instrumentation.py tests/test_agent.py tests/test_team_manager.py tests/test_postgres_gateway.py -v`

Expected: all selected tests PASS.

- [ ] **Step 7: Commit instrumentation**

```powershell
git add src/data_analysis_agent/tracing.py src/data_analysis_agent/agent.py src/data_analysis_agent/team.py src/data_analysis_agent/tools.py src/data_analysis_agent/postgres.py src/data_analysis_agent/api.py tests/test_trace_instrumentation.py
git commit -m "feat: record redacted runtime traces"
```

---

### Task 3: Trace and run APIs

**Files:**
- Modify: `src/data_analysis_agent/api.py`
- Modify: `web/src/types.ts`
- Modify: `web/src/api.ts`
- Modify: `tests/test_api.py`
- Modify: `web/src/api.test.ts`

**Interfaces:**
- Produces `GET /api/tasks/{task_id}/runs`, `GET /api/runs/{run_id}/trace`, and `POST /api/runs/{run_id}/accept`.
- Produces client methods `listRuns`, `getTrace`, and `acceptRun`.

- [ ] **Step 1: Write API tests proving replay is read-only**

```python
def test_trace_replay_returns_ordered_redacted_events_without_execution(traced_app, task) -> None:
    services = traced_app.state.services
    run = services.traces.start_run(task.id)
    services.trace_recorder.event(
        TraceContext(run.id, task.id),
        "tool.completed",
        "completed",
        {"dsn": "postgresql://secret", "row_count": 3},
    )
    provider_calls_before = traced_app.state.provider_calls
    with TestClient(traced_app) as client:
        response = client.get(f"/api/runs/{run.id}/trace")
    assert response.status_code == 200
    assert response.json()[0]["sequence"] == 1
    assert "secret" not in response.text
    assert traced_app.state.provider_calls == provider_calls_before


def test_only_completed_run_can_be_accepted(traced_app, task) -> None:
    run = traced_app.state.services.traces.start_run(task.id)
    with TestClient(traced_app) as client:
        response = client.post(f"/api/runs/{run.id}/accept")
    assert response.status_code == 409
```

- [ ] **Step 2: Run API tests and verify the expected failure**

Run: `python -m pytest tests/test_api.py -k 'trace or accept_run' -v`

Expected: FAIL because trace routes are absent.

- [ ] **Step 3: Add safe response models and routes**

Trace responses include event metadata, redacted payload, and artifact IDs. They exclude internal exception objects and all protected credential columns. Return `409` when acceptance preconditions fail.

```python
class TraceEventResponse(ApiModel):
    id: str
    run_id: str
    task_id: str
    agent_id: str | None
    parent_event_id: str | None
    sequence: int
    event_type: str
    status: str
    payload: dict[str, Any]
    duration_ms: int | None
    attempt: int
    error_category: str | None
    artifact_ids: list[str]


@app.get("/api/runs/{run_id}/trace", response_model=list[TraceEventResponse])
def get_trace(run_id: str) -> list[TraceEventResponse]:
    return [_trace_event_response(event) for event in services.traces.events_for_run(run_id)]
```

- [ ] **Step 4: Extend TypeScript types and client**

```typescript
export interface AnalysisRun {
  id: string;
  task_id: string;
  status: "running" | "completed" | "failed" | "cancelled";
  accepted: boolean;
  started_at: string;
  completed_at: string | null;
  error_category: string | null;
}

export interface TraceEvent {
  id: string;
  run_id: string;
  agent_id: string | null;
  parent_event_id: string | null;
  sequence: number;
  event_type: string;
  status: string;
  payload: Record<string, unknown>;
  duration_ms: number | null;
  attempt: number;
  error_category: string | null;
  artifact_ids: string[];
}
```

- [ ] **Step 5: Run backend and frontend client tests**

Run: `python -m pytest tests/test_api.py -v`

Run: `npm --prefix web test -- --run`

Expected: both commands PASS.

- [ ] **Step 6: Commit trace APIs**

```powershell
git add src/data_analysis_agent/api.py web/src/types.ts web/src/api.ts tests/test_api.py web/src/api.test.ts
git commit -m "feat: expose offline trace replay"
```

---

### Task 4: Workflow curator and candidate skill review

**Files:**
- Create: `src/data_analysis_agent/skills.py`
- Create: `tests/test_skill_curator.py`
- Modify: `src/data_analysis_agent/api.py`
- Modify: `web/src/types.ts`
- Modify: `web/src/api.ts`

**Interfaces:**
- Consumes: accepted successful `AnalysisRun`, ordered trace events, artifact evidence, and `ModelProvider`.
- Produces: `SkillCandidateRepository`, `SkillStaticValidator`, and `WorkflowCurator.create_candidate(run_id, model_name)`.
- Produces routes `POST /api/runs/{run_id}/skill-candidates`, `GET /api/skill-candidates/{id}`, and `POST /api/skill-candidates/{id}/decision`.

- [ ] **Step 1: Write eligibility and safety tests**

```python
def test_curator_rejects_unaccepted_run(curator, completed_run) -> None:
    with pytest.raises(SkillEligibilityError, match="accepted"):
        curator.create_candidate(completed_run.id, "scripted-model")


def test_curator_generates_disabled_sanitized_candidate(
    curator, accepted_run, candidate_repository
) -> None:
    candidate = curator.create_candidate(accepted_run.id, "scripted-model")
    assert candidate.status == "candidate"
    assert candidate.enabled is False
    assert "customer_email" not in candidate.markdown
    assert "postgresql://" not in candidate.markdown
    assert candidate.markdown.startswith("---\nname:")


@pytest.mark.parametrize("content", [
    "TO" + "DO: define workflow",
    "Use database password secret",
    "Automatically bypass approval",
])
def test_static_validator_rejects_unsafe_candidate(content: str) -> None:
    with pytest.raises(InvalidSkillCandidate):
        SkillStaticValidator().validate(content)
```

- [ ] **Step 2: Run curator tests and verify the expected failure**

Run: `python -m pytest tests/test_skill_curator.py -v`

Expected: FAIL because `skills.py` does not exist.

- [ ] **Step 3: Implement repository and eligibility gate**

Require `analysis_runs.status="completed"`, `accepted=1`, at least one evidence-linked report artifact, no trace event with unresolved `failed` status, and at least one completed SQL tool span. Calculate candidate version as `MAX(version) + 1` for `safe-sql-diagnostic` inside a transaction.

```python
@dataclass(frozen=True, slots=True)
class SkillCandidate:
    id: str
    source_run_id: str
    name: str
    version: int
    markdown: str
    status: str
    enabled: bool
    generated_by_model: str
    reviewed_at: str | None
    created_at: str
```

- [ ] **Step 4: Implement curator prompt and static validator**

The curator request contains only redacted event summaries, tool names, error categories, and artifact types. Require Markdown with YAML frontmatter fields `name` and `description`, plus sections `When to use`, `Inputs`, `Workflow`, `Safety gates`, and `Failure recovery`.

Reject incomplete-work markers (`"T" + "BD"` and `"TO" + "DO"`), credential-shaped URLs, known secret keys, customer-specific identifiers copied from trace payloads, instructions that disable approval, and missing safety sections. Persist source run, model name, version, and `enabled=0`.

```python
def create_candidate(self, run_id: str, model_name: str) -> SkillCandidate:
    run = self._require_eligible_run(run_id)
    summaries = self._redacted_summaries(self.traces.events_for_run(run.id))
    response = self.provider.complete(self._curator_request(summaries))
    markdown = response.text.strip()
    self.validator.validate(markdown)
    return self.candidates.create(run.id, "safe-sql-diagnostic", markdown, model_name)
```

- [ ] **Step 5: Add human decision API**

Creating a candidate returns `201`. Approval changes `status` to `approved` while keeping `enabled=false`; rejection changes `status` to `rejected`. A second decision returns `409`. The API never writes into the global Codex skill directory.

```python
class SkillCandidateDecision(ApiModel):
    approved: bool


@app.post("/api/skill-candidates/{candidate_id}/decision")
def decide_skill_candidate(candidate_id: str, request: SkillCandidateDecision):
    return services.skill_candidates.decide(candidate_id, request.approved)
```

Add matching TypeScript methods `createSkillCandidate(runId)`, `getSkillCandidate(candidateId)`, and `decideSkillCandidate(candidateId, approved)` plus this public type:

```typescript
export interface SkillCandidate {
  id: string;
  source_run_id: string;
  name: string;
  version: number;
  markdown: string;
  status: "candidate" | "approved" | "rejected";
  enabled: false;
  generated_by_model: string;
  reviewed_at: string | null;
  created_at: string;
}
```

- [ ] **Step 6: Run curator, API, and full backend tests**

Run: `python -m pytest tests/test_skill_curator.py tests/test_api.py -v`

Run: `python -m pytest -v`

Expected: both commands PASS.

- [ ] **Step 7: Commit skill curation**

```powershell
git add src/data_analysis_agent/skills.py src/data_analysis_agent/api.py web/src/types.ts web/src/api.ts tests/test_skill_curator.py
git commit -m "feat: curate reviewable skills from accepted traces"
```
