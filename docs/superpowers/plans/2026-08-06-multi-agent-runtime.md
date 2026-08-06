# Multi-Agent Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user-facing lead agent spawn at most three persistent, observable, role-scoped teammate runs and collect their structured results.

**Architecture:** Add durable teammate runs and messages to SQLite, then execute teammate loops in a bounded `ThreadPoolExecutor`. Teammates share project artifacts and the parent task ID but keep separate prompts and message histories; the lead uses explicit spawn, list, await, and cancel tools.

**Tech Stack:** Python 3.12, SQLite, `concurrent.futures.ThreadPoolExecutor`, existing model/tool protocols, FastAPI/SSE, pytest.

## Global Constraints

- The lead remains the only user-facing conversation owner.
- Allow at most three active teammates per parent task.
- Teammates cannot spawn teammates and receive role-specific tool allowlists.
- Every teammate has an independent message history, maximum turn count, wall-clock deadline, cancellation event, and terminal state.
- Persist state before starting background work so application restarts never leave an invisible agent.
- Treat forced process termination as `failed/interrupted`; do not claim automatic resume of an interrupted model call.
- Teammate database work still passes through the PostgreSQL safety gateway from the preceding plan.

---

### Task 1: Migration 4 and durable team repository

**Files:**
- Modify: `src/data_analysis_agent/db.py`
- Create: `src/data_analysis_agent/team.py`
- Create: `tests/test_team_repository.py`
- Modify: `tests/test_db.py`

**Interfaces:**
- Consumes: existing project/task IDs and `utc_now()`.
- Produces: `AgentRole`, `AgentRunStatus`, `AgentRun`, `AgentMessage`, and `TeamRepository`.
- Produces methods `create_run`, `get_run`, `list_runs`, `transition_run`, `append_message`, `list_messages`, `request_cancel`, and `mark_interrupted_runs`.

- [ ] **Step 1: Write migration and lifecycle tests**

```python
from data_analysis_agent.team import AgentRole, AgentRunStatus, TeamRepository


def test_team_migration_is_versioned(db_connection) -> None:
    names = {
        row[0] for row in db_connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {"agent_runs", "agent_messages"} <= names
    assert db_connection.execute(
        "SELECT MAX(version) FROM schema_migrations"
    ).fetchone() == (4,)


def test_repository_persists_independent_teammate_history(db_connection, task) -> None:
    repository = TeamRepository(db_connection)
    run = repository.create_run(task.id, AgentRole.SQL, "Aggregate regional profit")
    repository.append_message(run.id, "assistant", "Preparing a bounded query")

    assert repository.get_run(run.id).status is AgentRunStatus.CREATED
    assert repository.list_messages(run.id)[0].content == "Preparing a bounded query"
    assert task.id == repository.list_runs(task.id)[0].task_id


def test_invalid_run_transition_is_rejected(db_connection, task) -> None:
    repository = TeamRepository(db_connection)
    run = repository.create_run(task.id, AgentRole.REPORT, "Write report")
    repository.transition_run(run.id, AgentRunStatus.CANCELLED)
    with pytest.raises(InvalidAgentRunTransition):
        repository.transition_run(run.id, AgentRunStatus.WORKING)
```

- [ ] **Step 2: Run repository tests and verify the expected failure**

Run: `python -m pytest tests/test_team_repository.py tests/test_db.py -v`

Expected: FAIL because migration 4 and `team.py` do not exist.

- [ ] **Step 3: Add migration 4**

```python
MIGRATION_4_SQL = """
CREATE TABLE agent_runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    role TEXT NOT NULL CHECK(role IN ('knowledge', 'sql', 'report')),
    objective TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT,
    error_category TEXT,
    error_message TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT
);

CREATE TABLE agent_messages (
    id TEXT PRIMARY KEY,
    agent_run_id TEXT NOT NULL REFERENCES agent_runs(id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_agent_runs_task_created ON agent_runs(task_id, created_at);
CREATE INDEX idx_agent_messages_run_created ON agent_messages(agent_run_id, created_at);
"""
```

Apply it as schema version 4 after migration 3.

- [ ] **Step 4: Implement repository transitions**

Use these enums and transition map:

```python
class AgentRole(StrEnum):
    KNOWLEDGE = "knowledge"
    SQL = "sql"
    REPORT = "report"


class AgentRunStatus(StrEnum):
    CREATED = "created"
    WORKING = "working"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


RUN_TRANSITIONS = {
    AgentRunStatus.CREATED: {AgentRunStatus.WORKING, AgentRunStatus.CANCELLED},
    AgentRunStatus.WORKING: {
        AgentRunStatus.WAITING,
        AgentRunStatus.COMPLETED,
        AgentRunStatus.FAILED,
        AgentRunStatus.CANCELLED,
    },
    AgentRunStatus.WAITING: {
        AgentRunStatus.WORKING,
        AgentRunStatus.FAILED,
        AgentRunStatus.CANCELLED,
    },
    AgentRunStatus.COMPLETED: set(),
    AgentRunStatus.FAILED: set(),
    AgentRunStatus.CANCELLED: set(),
}
```

`mark_interrupted_runs()` changes persisted `working` and `waiting` rows to `failed` with `error_category="interrupted"` during service startup.

- [ ] **Step 5: Run repository tests**

Run: `python -m pytest tests/test_team_repository.py tests/test_db.py -v`

Expected: all selected tests PASS.

- [ ] **Step 6: Commit team persistence**

```powershell
git add src/data_analysis_agent/db.py src/data_analysis_agent/team.py tests/test_team_repository.py tests/test_db.py
git commit -m "feat: persist teammate runs and messages"
```

---

### Task 2: Role-scoped teammate loop

**Files:**
- Modify: `src/data_analysis_agent/team.py`
- Modify: `src/data_analysis_agent/tools.py`
- Modify: `src/data_analysis_agent/settings.py`
- Create: `tests/test_teammate_runner.py`

**Interfaces:**
- Consumes: `ModelProvider`, `ModelRequest`, `ToolRegistry`, `ToolContext`, and `TeamRepository`.
- Produces: `TeammateRunner.run(agent_run_id) -> AgentRun` and `ToolRegistry.restrict(allowed_names)`.
- Extends `ToolContext` with optional `agent_id` and `run_id` fields.

- [ ] **Step 1: Write role isolation and bounded-loop tests**

```python
def test_restricted_registry_exposes_only_sql_role_tools(tool_registry) -> None:
    restricted = tool_registry.restrict(
        {"inspect_workspace", "inspect_dataset", "query_data", "query_database"}
    )
    assert {item.name for item in restricted.schemas()} == {
        "inspect_workspace", "inspect_dataset", "query_data", "query_database"
    }
    result = restricted.execute(
        ToolCall(id="forbidden", name="spawn_teammate", arguments={}),
        ToolContext(project_id="p1", task_id="t1", agent_id="a1", run_id="r1"),
    )
    assert result.error_category == "unknown_tool"


def test_teammate_keeps_history_out_of_parent_conversation(
    team_repository, task_repository, task, teammate_runner_factory
) -> None:
    run = team_repository.create_run(task.id, AgentRole.KNOWLEDGE, "Find profit definition")
    completed = teammate_runner_factory(
        ScriptedProvider([ModelResponse(text='{"summary":"Use gross profit"}', tool_calls=())])
    ).run(run.id)

    assert completed.status is AgentRunStatus.COMPLETED
    assert json.loads(completed.result_json)["summary"] == "Use gross profit"
    assert task_repository.list_messages(task.id) == ()


def test_teammate_fails_after_turn_budget(team_repository, looping_teammate_runner, task) -> None:
    run = team_repository.create_run(task.id, AgentRole.SQL, "Loop forever")
    failed = looping_teammate_runner.run(run.id)
    assert failed.status is AgentRunStatus.FAILED
    assert failed.error_category == "agent_turn_limit"
```

- [ ] **Step 2: Run teammate tests and verify the expected failure**

Run: `python -m pytest tests/test_teammate_runner.py -v`

Expected: FAIL because `restrict`, extended context, and `TeammateRunner` are missing.

- [ ] **Step 3: Add role prompts and allowlists**

```python
ROLE_TOOLS = {
    AgentRole.KNOWLEDGE: frozenset({"inspect_workspace", "inspect_dataset"}),
    AgentRole.SQL: frozenset({
        "inspect_workspace", "inspect_dataset", "query_data", "query_database"
    }),
    AgentRole.REPORT: frozenset({
        "inspect_workspace", "run_python", "save_artifact", "write_report"
    }),
}

ROLE_PROMPTS = {
    AgentRole.KNOWLEDGE: "Resolve metric definitions and return a JSON handoff. Do not execute SQL.",
    AgentRole.SQL: "Produce evidence with governed query tools and return artifact identifiers.",
    AgentRole.REPORT: "Use registered artifacts only and cite every important claim.",
}
```

`ToolRegistry.restrict()` returns a new registry containing only named existing definitions and the same permission, approval, task, and artifact services.

- [ ] **Step 4: Implement the teammate loop**

Add settings:

```python
max_teammate_turns: int = 12
teammate_timeout_seconds: int = 120
max_active_teammates: int = 3
```

For every model call, rebuild the request from `TeamRepository.list_messages(agent_run_id)`. Persist assistant tool calls and tool results in `agent_messages`. Check `cancel_requested`, elapsed monotonic time, and turn count before each call and tool execution. Require the final assistant text to be a JSON object; store it in `agent_runs.result_json`. Invalid JSON ends with `error_category="invalid_handoff"`.

The loop shape is:

```python
def run(self, agent_run_id: str) -> AgentRun:
    run = self.repository.transition_run(agent_run_id, AgentRunStatus.WORKING)
    deadline = monotonic() + self.settings.teammate_timeout_seconds
    for turn in range(1, self.settings.max_teammate_turns + 1):
        if self.cancel_event.is_set() or self.repository.cancel_requested(run.id):
            return self.repository.transition_run(run.id, AgentRunStatus.CANCELLED)
        if monotonic() >= deadline:
            return self.repository.fail_run(run.id, "agent_timeout", "Teammate deadline reached")
        response = self.provider.complete(self._request(run))
        self._persist_response(run, response)
        if not response.tool_calls:
            return self.repository.complete_run(run.id, self._parse_handoff(response.text))
        self._execute_calls(run, response.tool_calls)
    return self.repository.fail_run(run.id, "agent_turn_limit", "Teammate turn limit reached")
```

- [ ] **Step 5: Run teammate and existing tool tests**

Run: `python -m pytest tests/test_teammate_runner.py tests/test_tools.py tests/test_agent.py -v`

Expected: all selected tests PASS.

- [ ] **Step 6: Commit the role-scoped loop**

```powershell
git add src/data_analysis_agent/team.py src/data_analysis_agent/tools.py src/data_analysis_agent/settings.py tests/test_teammate_runner.py
git commit -m "feat: add bounded role scoped teammate loop"
```

---

### Task 3: Threaded manager and lead coordination tools

**Files:**
- Modify: `src/data_analysis_agent/team.py`
- Modify: `src/data_analysis_agent/tools.py`
- Modify: `src/data_analysis_agent/api.py`
- Create: `tests/test_team_manager.py`
- Modify: `tests/test_tools.py`

**Interfaces:**
- Consumes: `TeammateRunner`, `TeamRepository`, and application provider/tool factories.
- Produces: `TeamManager.spawn`, `list`, `await_runs`, `cancel`, and `shutdown`.
- Produces lead tools `spawn_teammate`, `list_teammates`, `await_teammates`, and `cancel_teammate`.

- [ ] **Step 1: Write manager concurrency and limit tests**

```python
def test_manager_runs_two_teammates_concurrently(team_manager, task) -> None:
    knowledge = team_manager.spawn(task.id, AgentRole.KNOWLEDGE, "Resolve metrics")
    sql = team_manager.spawn(task.id, AgentRole.SQL, "Query profit")
    completed = team_manager.await_runs((knowledge.id, sql.id), timeout_seconds=2)
    assert {item.status for item in completed} == {AgentRunStatus.COMPLETED}


def test_manager_enforces_active_limit(team_manager, task) -> None:
    for index in range(3):
        team_manager.spawn(task.id, AgentRole.SQL, f"Query {index}")
    with pytest.raises(TeamCapacityError):
        team_manager.spawn(task.id, AgentRole.REPORT, "Fourth teammate")


def test_cancel_sets_signal_and_terminal_state(blocking_team_manager, task) -> None:
    run = blocking_team_manager.spawn(task.id, AgentRole.SQL, "Long query")
    blocking_team_manager.cancel(run.id)
    terminal = blocking_team_manager.await_runs((run.id,), timeout_seconds=2)[0]
    assert terminal.status is AgentRunStatus.CANCELLED
```

- [ ] **Step 2: Run manager tests and verify the expected failure**

Run: `python -m pytest tests/test_team_manager.py -v`

Expected: FAIL because `TeamManager` does not exist.

- [ ] **Step 3: Implement manager lifecycle**

Construct `ThreadPoolExecutor(max_workers=settings.max_active_teammates)`. `spawn()` must create the database row before submitting `_execute(run.id)`. Keep `Future` and `threading.Event` objects in dictionaries protected by `threading.Lock`. `shutdown()` requests cancellation, calls `executor.shutdown(wait=False, cancel_futures=True)`, and does not delete durable run records.

Use a `runner_factory: Callable[[AgentRole], TeammateRunner]` so tests can supply role-specific scripted providers and production can create a fresh provider plus fresh SQLite connection per teammate thread.

```python
class TeamManager:
    def spawn(self, task_id: str, role: AgentRole, objective: str) -> AgentRun:
        if self.repository.active_count(task_id) >= self.settings.max_active_teammates:
            raise TeamCapacityError("At most three teammates may be active")
        run = self.repository.create_run(task_id, role, objective)
        cancel_event = Event()
        with self._lock:
            self._cancel_events[run.id] = cancel_event
            self._futures[run.id] = self._executor.submit(self._execute, run.id, role, cancel_event)
        return run

    def await_runs(self, run_ids: tuple[str, ...], timeout_seconds: int) -> tuple[AgentRun, ...]:
        futures = [self._owned_future(run_id) for run_id in run_ids]
        wait(futures, timeout=timeout_seconds)
        return tuple(self.repository.get_run(run_id) for run_id in run_ids)
```

- [ ] **Step 4: Add lead tool inputs and handlers**

```python
class SpawnTeammateInput(ToolInput):
    role: AgentRole
    objective: str = Field(min_length=1, max_length=2_000)


class AwaitTeammatesInput(ToolInput):
    agent_ids: list[str] = Field(min_length=1, max_length=3)
    timeout_seconds: int = Field(default=30, ge=1, le=120)
```

`spawn_teammate` returns the durable agent ID immediately. `await_teammates` returns role, status, structured result, and safe error for every requested run. `list_teammates` never blocks. `cancel_teammate` can cancel only runs belonging to the current parent task.

- [ ] **Step 5: Wire startup and shutdown**

Add `TeamRepository` and `TeamManager` to `AppServices`. During FastAPI lifespan startup call `mark_interrupted_runs()` before accepting tasks; during shutdown call `team_manager.shutdown()` before closing the shared application connection.

```python
@asynccontextmanager
async def lifespan(_: FastAPI):
    services.team_repository.mark_interrupted_runs()
    try:
        yield
    finally:
        services.team_manager.shutdown()
        services.connection.close()
```

- [ ] **Step 6: Run team, tools, agent, and API tests**

Run: `python -m pytest tests/test_team_manager.py tests/test_teammate_runner.py tests/test_tools.py tests/test_agent.py tests/test_api.py -v`

Expected: all selected tests PASS.

- [ ] **Step 7: Commit coordination tools**

```powershell
git add src/data_analysis_agent/team.py src/data_analysis_agent/tools.py src/data_analysis_agent/api.py tests/test_team_manager.py tests/test_tools.py
git commit -m "feat: let lead coordinate teammate threads"
```

---

### Task 4: Team status API and SSE events

**Files:**
- Modify: `src/data_analysis_agent/api.py`
- Modify: `src/data_analysis_agent/team.py`
- Modify: `web/src/types.ts`
- Modify: `web/src/api.ts`
- Modify: `tests/test_api.py`
- Modify: `web/src/api.test.ts`

**Interfaces:**
- Consumes: durable team repository.
- Produces `GET /api/tasks/{task_id}/agents` and `ApiClient.listAgents(taskId)`.
- Adds SSE events `agent.created`, `agent.state`, `agent.message`, and `agent.completed`.

- [ ] **Step 1: Write API response and SSE tests**

```python
def test_agents_endpoint_lists_safe_teammate_state(app_with_team, task) -> None:
    run = app_with_team.state.services.team_manager.spawn(
        task.id, AgentRole.SQL, "Aggregate profit"
    )
    with TestClient(app_with_team) as client:
        response = client.get(f"/api/tasks/{task.id}/agents")
    assert response.status_code == 200
    assert response.json()[0]["id"] == run.id
    assert "messages" not in response.json()[0]
    assert "credentials" not in response.text
```

Add an event assertion that the task event stream contains `agent.created` followed by a terminal `agent.state` event with the same `agent_id`.

- [ ] **Step 2: Run API tests and verify the expected failure**

Run: `python -m pytest tests/test_api.py -k agent -v`

Expected: FAIL because the endpoint and events are absent.

- [ ] **Step 3: Implement safe response models and events**

Expose only ID, role, objective, status, result summary, safe error category, and timestamps. `TeamManager` appends parent-task events at every transition. Never serialize teammate model messages through this endpoint.

```python
class AgentRunResponse(ApiModel):
    id: str
    task_id: str
    role: str
    objective: str
    status: str
    result_summary: str | None
    error_category: str | None
    created_at: str
    started_at: str | None
    completed_at: str | None


@app.get("/api/tasks/{task_id}/agents", response_model=list[AgentRunResponse])
def list_agents(task_id: str) -> list[AgentRunResponse]:
    services.tasks.get(task_id)
    return [_agent_run_response(run) for run in services.team_repository.list_runs(task_id)]
```

- [ ] **Step 4: Extend the TypeScript client**

```typescript
export interface AgentRun {
  id: string;
  task_id: string;
  role: "knowledge" | "sql" | "report";
  objective: string;
  status: "created" | "working" | "waiting" | "completed" | "failed" | "cancelled";
  result_summary: string | null;
  error_category: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}
```

Add `listAgents(taskId: string): Promise<AgentRun[]>` to `ApiClient` and `HttpApiClient`; extend `TaskEventType` with the four agent events.

- [ ] **Step 5: Run backend and frontend client tests**

Run: `python -m pytest tests/test_api.py -v`

Run: `npm --prefix web test -- --run`

Expected: both commands PASS.

- [ ] **Step 6: Commit the observable API**

```powershell
git add src/data_analysis_agent/api.py src/data_analysis_agent/team.py web/src/types.ts web/src/api.ts tests/test_api.py web/src/api.test.ts
git commit -m "feat: expose live teammate status"
```
