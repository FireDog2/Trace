# Data Analysis Agent MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local-first analysis project workspace that turns a CSV or Excel business question into a persisted plan, executable analysis, traceable artifacts, and an evidence-linked Markdown report.

**Architecture:** A React workbench communicates over HTTP and SSE with a FastAPI service. The service owns a SQLite metadata store, contained project workspaces, DuckDB and controlled Python tools, and a provider-neutral model-controlled agent loop.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, SQLite, DuckDB, Polars, openpyxl, Plotly, pytest, React, TypeScript, Vite, Vitest, Testing Library.

## Global Constraints

- Python runtime is exactly Python 3.12 for development and packaging.
- Source datasets are immutable; transformations always create derived artifacts.
- Model providers never leak provider-specific response objects into domain or tool modules.
- The model has no arbitrary shell, unrestricted filesystem, external network, or destructive database tool.
- Python execution is locally trusted and contained by path, timeout, and subprocess controls; it is not described as hostile-code isolation.
- SQLite schema migrations are present from the first executable release.
- Deterministic tests run without a model API key.
- The initial report format is Markdown and every artifact reference is validated before publication.
- `Data-Analysis-Agent` is a product reference only; its commercially restricted source code is not copied.

## File Map

### Backend and shared project files

- `pyproject.toml`: package metadata, runtime dependencies, pytest configuration, and development tools.
- `.gitignore`: Python, Node, local workspace, database, and generated-output exclusions.
- `README.md`: local setup, run commands, trust model, and MVP workflow.
- `src/data_analysis_agent/__init__.py`: package version.
- `src/data_analysis_agent/settings.py`: typed application paths and execution limits.
- `src/data_analysis_agent/domain.py`: shared enums and immutable domain records.
- `src/data_analysis_agent/db.py`: SQLite connection and ordered migrations.
- `src/data_analysis_agent/repositories.py`: persistence operations for projects, tasks, messages, plans, executions, events, and artifacts.
- `src/data_analysis_agent/workspace.py`: workspace creation, contained path resolution, and source/derived file placement.
- `src/data_analysis_agent/datasets.py`: CSV and Excel registration and structural inspection.
- `src/data_analysis_agent/permissions.py`: action classification and approval records.
- `src/data_analysis_agent/execution.py`: read-only DuckDB and controlled Python execution.
- `src/data_analysis_agent/tools.py`: typed model-tool definitions and dispatch registry.
- `src/data_analysis_agent/providers.py`: provider-neutral protocol, deterministic scripted provider, and OpenAI-compatible adapter.
- `src/data_analysis_agent/agent.py`: model-controlled tool-use loop and task state transitions.
- `src/data_analysis_agent/reporting.py`: Markdown evidence-reference validation and publication.
- `src/data_analysis_agent/api.py`: FastAPI application, HTTP endpoints, and SSE stream.
- `src/data_analysis_agent/cli.py`: local API startup entry point.

### Frontend

- `web/package.json`: frontend scripts and dependencies.
- `web/vite.config.ts`: Vite and test configuration.
- `web/tsconfig.json`: strict TypeScript settings.
- `web/index.html`: application entry document.
- `web/src/main.tsx`: React bootstrapping.
- `web/src/types.ts`: API and event contracts.
- `web/src/api.ts`: HTTP client and SSE subscription.
- `web/src/App.tsx`: project navigation, task workbench, and conversation composition.
- `web/src/components/ProjectNav.tsx`: datasets and tasks navigation.
- `web/src/components/TaskWorkbench.tsx`: objective, plan, timeline, and artifact panels.
- `web/src/components/Conversation.tsx`: message input and approval interaction.
- `web/src/styles.css`: intentional three-area workbench layout.

### Tests and fixtures

- `tests/conftest.py`: isolated settings, temporary workspaces, and database fixtures.
- `tests/test_db.py`: migration and persistence tests.
- `tests/test_workspace.py`: containment and immutability tests.
- `tests/test_datasets.py`: CSV and Excel registration and inspection tests.
- `tests/test_tasks.py`: task states, plan revisions, messages, and event ordering.
- `tests/test_permissions.py`: allow, approval, and deny policy tests.
- `tests/test_execution.py`: read-only query, Python timeout, and output-containment tests.
- `tests/test_tools.py`: tool schema, dispatch, and artifact provenance tests.
- `tests/test_agent.py`: deterministic loop, retries, input pause, and completion tests.
- `tests/test_reporting.py`: evidence validation tests.
- `tests/test_api.py`: project-to-task HTTP and SSE integration tests.
- `tests/test_acceptance.py`: complete operating-diagnosis workflow.
- `tests/fixtures/operations.csv`: small deterministic regional operating dataset.
- `tests/fixtures/operations.xlsx`: equivalent Excel fixture generated by the dataset test.
- `web/src/App.test.tsx`: frontend workflow rendering test.

---

### Task 1: Executable Backend Foundation

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `src/data_analysis_agent/__init__.py`
- Create: `src/data_analysis_agent/settings.py`
- Create: `src/data_analysis_agent/domain.py`
- Create: `src/data_analysis_agent/db.py`
- Create: `tests/conftest.py`
- Create: `tests/test_db.py`

**Interfaces:**
- Produces: `Settings(data_dir: Path, database_path: Path, python_timeout_seconds: int, max_tool_actions: int)`.
- Produces: `connect_database(settings: Settings) -> sqlite3.Connection`.
- Produces: `migrate(connection: sqlite3.Connection) -> None` creating schema version 1.
- Produces: shared `ProjectStatus`, `TaskStatus`, `StepStatus`, `ArtifactType`, and `ExecutionStatus` string enums.

- [ ] **Step 1: Write the database migration test**

```python
def test_migrate_creates_versioned_core_tables(db_connection):
    names = {
        row[0]
        for row in db_connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {
        "schema_migrations", "projects", "tasks", "messages",
        "plan_revisions", "plan_steps", "tool_executions",
        "artifacts", "artifact_inputs", "evidence_links",
        "approvals", "task_events",
    } <= names
    assert db_connection.execute(
        "SELECT version FROM schema_migrations"
    ).fetchall() == [(1,)]
```

- [ ] **Step 2: Run the test and confirm the missing package failure**

Run: `python -m pytest tests/test_db.py -v`

Expected: FAIL because `data_analysis_agent` is not importable.

- [ ] **Step 3: Add package metadata, settings, enums, and migration 1**

Use a `src` package layout. Migration 1 must create all tables named in Step 1, use text UUID primary keys, enable foreign keys, store timestamps as UTC ISO-8601 text, and create an index on `task_events(task_id, sequence)`.

```python
def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied = {
        row[0] for row in connection.execute("SELECT version FROM schema_migrations")
    }
    if 1 not in applied:
        connection.executescript(MIGRATION_1_SQL)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (1, utc_now()),
        )
    connection.commit()
```

- [ ] **Step 4: Run foundation tests**

Run: `python -m pytest tests/test_db.py -v`

Expected: PASS.

- [ ] **Step 5: Commit the foundation**

```powershell
git add pyproject.toml .gitignore src/data_analysis_agent tests/conftest.py tests/test_db.py
git commit -m "feat: establish backend foundation"
```

### Task 2: Project Workspace and Artifact Persistence

**Files:**
- Create: `src/data_analysis_agent/repositories.py`
- Create: `src/data_analysis_agent/workspace.py`
- Create: `tests/test_workspace.py`

**Interfaces:**
- Consumes: `Settings`, `connect_database`, `ArtifactType`.
- Produces: `ProjectRepository.create(name: str) -> Project` and `ProjectRepository.get(project_id: str) -> Project`.
- Produces: `WorkspaceService.create_project(project_id: str) -> Path`.
- Produces: `WorkspaceService.resolve_inside(project_id: str, relative_path: str) -> Path`.
- Produces: `WorkspaceService.import_source(project_id: str, source: Path) -> Path` that copies without later mutation.
- Produces: `ArtifactRepository.register(project_id: str, task_id: str | None, artifact_type: ArtifactType, path: Path, title: str, description: str, producer_execution_id: str | None, input_artifact_ids: tuple[str, ...]) -> Artifact`.

- [ ] **Step 1: Write containment and source immutability tests**

```python
def test_resolve_inside_rejects_parent_escape(workspace, project):
    with pytest.raises(WorkspaceBoundaryError):
        workspace.resolve_inside(project.id, "../outside.csv")


def test_import_source_copies_bytes_and_registers_separate_path(
    workspace, tmp_path, project
):
    incoming = tmp_path / "sales.csv"
    incoming.write_text("region,revenue\nEast,10\n", encoding="utf-8")
    imported = workspace.import_source(project.id, incoming)
    incoming.write_text("changed", encoding="utf-8")
    assert imported.read_text(encoding="utf-8") == "region,revenue\nEast,10\n"
    assert imported.parent.name == "source"
```

- [ ] **Step 2: Run tests and confirm missing workspace services**

Run: `python -m pytest tests/test_workspace.py -v`

Expected: FAIL because the workspace and repositories are not implemented.

- [ ] **Step 3: Implement repositories and contained workspace paths**

Create project directories `source`, `derived`, `code`, `charts`, `reports`, `logs`, and `knowledge`. Resolve both the project root and candidate path before comparing ancestry.

```python
def resolve_inside(self, project_id: str, relative_path: str) -> Path:
    root = self.project_root(project_id).resolve()
    candidate = (root / relative_path).resolve()
    if candidate != root and root not in candidate.parents:
        raise WorkspaceBoundaryError(relative_path)
    return candidate
```

Artifact registration must reject nonexistent files, paths outside the project, and input artifacts from another project.

- [ ] **Step 4: Run workspace tests**

Run: `python -m pytest tests/test_workspace.py -v`

Expected: PASS.

- [ ] **Step 5: Commit workspace persistence**

```powershell
git add src/data_analysis_agent/repositories.py src/data_analysis_agent/workspace.py tests/test_workspace.py
git commit -m "feat: add contained analysis workspaces"
```

### Task 3: Dataset Registration and Inspection

**Files:**
- Create: `src/data_analysis_agent/datasets.py`
- Create: `tests/test_datasets.py`
- Create: `tests/fixtures/operations.csv`

**Interfaces:**
- Consumes: `WorkspaceService.import_source`, `ArtifactRepository.register`.
- Produces: `DatasetService.add_file(project_id: str, source: Path) -> Artifact` for `.csv` and `.xlsx` only.
- Produces: `DatasetService.inspect(artifact_id: str, sample_rows: int = 20) -> DatasetInspection`.
- Produces: `DatasetInspection` with `sheets`, `columns`, `row_count`, `sample`, `null_counts`, and `duplicate_row_count`.

- [ ] **Step 1: Add a deterministic operating dataset and failing tests**

The CSV fixture contains columns `month`, `region`, `category`, `revenue`, `product_cost`, `logistics_cost`, and `profit`, with at least 12 rows and an intentional East-region logistics-cost increase.

```python
def test_add_and_inspect_csv(dataset_service, project, operations_csv):
    artifact = dataset_service.add_file(project.id, operations_csv)
    inspection = dataset_service.inspect(artifact.id, sample_rows=3)
    assert artifact.artifact_type == ArtifactType.SOURCE_DATASET
    assert inspection.row_count >= 12
    assert [column.name for column in inspection.columns][:2] == ["month", "region"]
    assert len(inspection.sample) == 3


def test_add_and_inspect_excel(dataset_service, project, operations_csv, tmp_path):
    frame = pl.read_csv(operations_csv)
    workbook = tmp_path / "operations.xlsx"
    frame.write_excel(workbook, worksheet="operations")
    artifact = dataset_service.add_file(project.id, workbook)
    inspection = dataset_service.inspect(artifact.id)
    assert inspection.sheets == ("operations",)
    assert inspection.row_count >= 12
```

- [ ] **Step 2: Run dataset tests**

Run: `python -m pytest tests/test_datasets.py -v`

Expected: FAIL because `DatasetService` is missing.

- [ ] **Step 3: Implement CSV and Excel adapters**

Use Polars for CSV profiling and `openpyxl` in read-only mode to enumerate Excel worksheets. Convert worksheet rows to a Polars frame for the inspection result. Reject files by both extension and parser failure with `UnsupportedDatasetError` or `InvalidDatasetError`.

Inspection must cap samples at 100 rows and return JSON-compatible scalar values.

- [ ] **Step 4: Run dataset tests**

Run: `python -m pytest tests/test_datasets.py -v`

Expected: PASS.

- [ ] **Step 5: Commit dataset support**

```powershell
git add src/data_analysis_agent/datasets.py tests/test_datasets.py tests/fixtures/operations.csv
git commit -m "feat: inspect csv and excel datasets"
```

### Task 4: Tasks, Plans, Messages, and Ordered Events

**Files:**
- Modify: `src/data_analysis_agent/repositories.py`
- Create: `tests/test_tasks.py`

**Interfaces:**
- Produces: `TaskRepository.create(project_id: str, objective: str) -> Task`.
- Produces: `TaskRepository.transition(task_id: str, target: TaskStatus) -> Task` with validated transitions.
- Produces: `TaskRepository.add_message(task_id: str, role: str, content: str) -> Message`.
- Produces: `TaskRepository.revise_plan(task_id: str, steps: tuple[PlanStepDraft, ...], reason: str) -> PlanRevision`.
- Produces: `TaskRepository.append_event(task_id: str, event_type: str, payload: dict[str, Any]) -> TaskEvent` with monotonically increasing per-task sequence.
- Produces: `TaskRepository.events_after(task_id: str, sequence: int) -> tuple[TaskEvent, ...]`.

- [ ] **Step 1: Write transition, revision, and ordering tests**

```python
def test_task_rejects_invalid_transition(task_repository, project):
    task = task_repository.create(project.id, "Diagnose East profit decline")
    with pytest.raises(InvalidTaskTransition):
        task_repository.transition(task.id, TaskStatus.COMPLETED)


def test_plan_revisions_preserve_history(task_repository, task):
    first = task_repository.revise_plan(
        task.id, (PlanStepDraft("Inspect data"),), "Initial plan"
    )
    second = task_repository.revise_plan(
        task.id,
        (PlanStepDraft("Inspect data"), PlanStepDraft("Compare regions")),
        "Data supports a regional comparison",
    )
    assert second.revision == first.revision + 1
    assert len(task_repository.get_plan_revision(first.id).steps) == 1


def test_events_have_monotonic_task_sequence(task_repository, task):
    one = task_repository.append_event(task.id, "task.started", {})
    two = task_repository.append_event(task.id, "plan.updated", {"revision": 1})
    assert (one.sequence, two.sequence) == (1, 2)
```

- [ ] **Step 2: Run task tests**

Run: `python -m pytest tests/test_tasks.py -v`

Expected: FAIL because task persistence methods are missing.

- [ ] **Step 3: Implement transactional task operations**

Allowed transitions are explicitly mapped. `needs_input` may return to the prior active phase recorded in event payload; `failed` may return only to `running`; terminal states do not transition.

Use `BEGIN IMMEDIATE` while assigning plan revision and event sequence numbers so concurrent requests cannot create duplicates.

- [ ] **Step 4: Run task tests**

Run: `python -m pytest tests/test_tasks.py -v`

Expected: PASS.

- [ ] **Step 5: Commit task persistence**

```powershell
git add src/data_analysis_agent/repositories.py tests/test_tasks.py
git commit -m "feat: persist analysis tasks and events"
```

### Task 5: Permission Policy and Approval Persistence

**Files:**
- Create: `src/data_analysis_agent/permissions.py`
- Create: `tests/test_permissions.py`

**Interfaces:**
- Consumes: project root paths and tool-call arguments.
- Produces: `PermissionPolicy.classify(action: ToolAction) -> PermissionDecision`.
- Produces: `PermissionLevel.ALLOW`, `PermissionLevel.REQUIRE_APPROVAL`, or `PermissionLevel.DENY`.
- Produces: `ApprovalRepository.request(task_id: str, action: ToolAction, reason: str) -> Approval` and `ApprovalRepository.decide(approval_id: str, approved: bool) -> Approval`.

- [ ] **Step 1: Write policy matrix tests**

```python
@pytest.mark.parametrize("tool_name", [
    "inspect_workspace", "inspect_dataset", "query_data",
    "run_python", "save_artifact", "write_report", "update_plan",
])
def test_low_risk_project_actions_are_allowed(policy, tool_name):
    decision = policy.classify(ToolAction(tool_name, {"project_id": "p1"}))
    assert decision.level is PermissionLevel.ALLOW


def test_external_path_requires_approval(policy, tmp_path):
    decision = policy.classify(
        ToolAction("read_external_file", {"path": str(tmp_path / "outside.csv")})
    )
    assert decision.level is PermissionLevel.REQUIRE_APPROVAL


def test_source_overwrite_is_denied(policy):
    decision = policy.classify(
        ToolAction("run_python", {"declared_outputs": ["source/data.csv"]})
    )
    assert decision.level is PermissionLevel.DENY
```

- [ ] **Step 2: Run permission tests**

Run: `python -m pytest tests/test_permissions.py -v`

Expected: FAIL because the policy is missing.

- [ ] **Step 3: Implement explicit policy rules**

Unknown tools default to `DENY`. Project-local derived outputs are allowed. Existing artifact overwrite, external paths, network, and dependency installation require approval unless the action would mutate source data, escape configured roots, or perform destructive SQL, which is denied.

- [ ] **Step 4: Run permission tests**

Run: `python -m pytest tests/test_permissions.py -v`

Expected: PASS.

- [ ] **Step 5: Commit permission policy**

```powershell
git add src/data_analysis_agent/permissions.py tests/test_permissions.py
git commit -m "feat: enforce analysis permission policy"
```

### Task 6: DuckDB and Controlled Python Execution

**Files:**
- Create: `src/data_analysis_agent/execution.py`
- Create: `tests/test_execution.py`

**Interfaces:**
- Consumes: registered dataset artifacts and task workspace paths.
- Produces: `DuckDBExecutor.query(project_id: str, sql: str, bindings: Mapping[str, str]) -> QueryResult`.
- Produces: `PythonExecutor.run(task_id: str, code: str, declared_outputs: tuple[str, ...]) -> PythonResult`.
- `QueryResult` includes `columns`, up to 200 preview rows, total materialized row count, and optional derived artifact.
- `PythonResult` includes exit code, bounded stdout, bounded stderr, duration, code artifact, and registered declared outputs.

- [ ] **Step 1: Write read-only SQL and Python boundary tests**

```python
def test_duckdb_rejects_mutating_sql(duckdb_executor, dataset_artifact):
    with pytest.raises(ReadOnlyQueryError):
        duckdb_executor.query(
            dataset_artifact.project_id,
            "DROP TABLE operations",
            {"operations": dataset_artifact.id},
        )


def test_python_timeout_is_structured(python_executor, task):
    result = python_executor.run(task.id, "while True: pass", ())
    assert result.status is ExecutionStatus.TIMED_OUT
    assert "time limit" in result.error_message.lower()


def test_python_rejects_undeclared_output(python_executor, task):
    result = python_executor.run(
        task.id,
        "from pathlib import Path\nPath('derived/hidden.txt').write_text('x')",
        (),
    )
    assert result.status is ExecutionStatus.FAILED
    assert "undeclared output" in result.error_message.lower()
```

- [ ] **Step 2: Run execution tests**

Run: `python -m pytest tests/test_execution.py -v`

Expected: FAIL because executors are missing.

- [ ] **Step 3: Implement read-only DuckDB execution**

Reject SQL containing more than one statement and require its parsed leading operation to be `SELECT`, `WITH`, `DESCRIBE`, `SUMMARIZE`, or `EXPLAIN`. Register artifact files under generated safe DuckDB view names rather than interpolating user paths into SQL.

- [ ] **Step 4: Implement controlled Python subprocess execution**

Persist generated code before execution. Run `sys.executable` with `-I`, a task execution directory as `cwd`, a minimal environment, captured output, and the configured timeout. Snapshot workspace files before and after execution; fail if created or modified files are outside declared derived paths, and never accept changes under `source`.

- [ ] **Step 5: Run execution tests**

Run: `python -m pytest tests/test_execution.py -v`

Expected: PASS.

- [ ] **Step 6: Commit execution engines**

```powershell
git add src/data_analysis_agent/execution.py tests/test_execution.py
git commit -m "feat: execute read-only sql and contained python"
```

### Task 7: Tool Registry, Reporting, and Agent Loop

**Files:**
- Create: `src/data_analysis_agent/tools.py`
- Create: `src/data_analysis_agent/reporting.py`
- Create: `src/data_analysis_agent/providers.py`
- Create: `src/data_analysis_agent/agent.py`
- Create: `tests/test_tools.py`
- Create: `tests/test_reporting.py`
- Create: `tests/test_agent.py`

**Interfaces:**
- Produces: `ModelProvider.complete(request: ModelRequest) -> ModelResponse` protocol.
- Produces: `ScriptedProvider(responses: Iterable[ModelResponse])` for deterministic tests.
- Produces: `ToolRegistry.schemas() -> tuple[ToolSchema, ...]` and `ToolRegistry.execute(call: ToolCall, context: ToolContext) -> ToolResult`.
- Produces: `ReportService.publish(task_id: str, markdown: str) -> Artifact`.
- Produces: `AgentRunner.run(task_id: str) -> Task` and `AgentRunner.resume(task_id: str, user_message: str) -> Task`.

- [ ] **Step 1: Write report evidence tests**

```python
def test_publish_rejects_missing_artifact_reference(report_service, task):
    markdown = "Revenue declined. [evidence](artifact://missing)"
    with pytest.raises(InvalidEvidenceReference):
        report_service.publish(task.id, markdown)


def test_publish_records_valid_evidence_link(
    report_service, task, chart_artifact, artifact_repository
):
    markdown = f"Costs increased. [evidence](artifact://{chart_artifact.id})"
    report = report_service.publish(task.id, markdown)
    assert artifact_repository.evidence_for(report.id) == (chart_artifact.id,)
```

- [ ] **Step 2: Write deterministic agent-loop tests**

```python
def test_agent_executes_tools_and_completes(agent_factory, task):
    provider = ScriptedProvider([
        response_with_tool("update_plan", {"steps": ["Inspect data"]}),
        response_with_tool("inspect_workspace", {}),
        response_with_text("Analysis completed"),
    ])
    completed = agent_factory(provider).run(task.id)
    assert completed.status is TaskStatus.COMPLETED


def test_agent_pauses_for_business_input(agent_factory, task):
    provider = ScriptedProvider([
        response_with_tool(
            "request_user_input",
            {"question": "Which profit definition should be used?"},
        )
    ])
    paused = agent_factory(provider).run(task.id)
    assert paused.status is TaskStatus.NEEDS_INPUT
```

- [ ] **Step 3: Run tool, report, and agent tests**

Run: `python -m pytest tests/test_tools.py tests/test_reporting.py tests/test_agent.py -v`

Expected: FAIL because the registry, report service, providers, and runner are missing.

- [ ] **Step 4: Implement typed tool registry and report service**

Register exactly the eight MVP tools from the design. Validate every argument with a Pydantic input model before permission classification. Convert exceptions to structured `ToolResult(error_category, message, retryable, diagnostics)` without stack traces or secrets.

Report publication extracts `artifact://<uuid>` references, verifies project ownership, requires at least one valid reference when the report contains a `## Key Findings` section, writes a versioned Markdown file, and records evidence links transactionally.

- [ ] **Step 5: Implement the provider-neutral agent loop**

```python
for action_number in range(settings.max_tool_actions):
    response = provider.complete(context_builder.build(task_id, tool_registry.schemas()))
    task_repository.add_assistant_response(task_id, response)
    if response.tool_calls:
        for call in response.tool_calls:
            result = tool_registry.execute(call, tool_context)
            task_repository.record_tool_result(task_id, call, result)
            if result.pauses_task:
                return task_repository.get(task_id)
        continue
    return task_repository.complete(task_id, response.text)
return task_repository.fail(task_id, "tool_action_limit", "Tool action limit reached")
```

If a tool fails retryably, the structured result returns to the model. The runner permits at most two consecutive calls with the same tool name and normalized arguments. Persisted task events are emitted for state, plan, tool, artifact, approval, and message changes.

- [ ] **Step 6: Run agent subsystem tests**

Run: `python -m pytest tests/test_tools.py tests/test_reporting.py tests/test_agent.py -v`

Expected: PASS.

- [ ] **Step 7: Commit agent runtime**

```powershell
git add src/data_analysis_agent/tools.py src/data_analysis_agent/reporting.py src/data_analysis_agent/providers.py src/data_analysis_agent/agent.py tests/test_tools.py tests/test_reporting.py tests/test_agent.py
git commit -m "feat: add traceable analysis agent loop"
```

### Task 8: FastAPI and Server-Sent Events

**Files:**
- Create: `src/data_analysis_agent/api.py`
- Create: `src/data_analysis_agent/cli.py`
- Create: `tests/test_api.py`

**Interfaces:**
- Consumes: project, dataset, task, artifact, approval, and agent services.
- Produces: `create_app(settings: Settings, provider: ModelProvider | None = None) -> FastAPI`.
- Produces HTTP endpoints under `/api/projects`, `/api/tasks`, `/api/artifacts`, and `/api/approvals`.
- Produces SSE endpoint `GET /api/tasks/{task_id}/events?after=<sequence>`.

- [ ] **Step 1: Write the API lifecycle test**

```python
def test_project_dataset_task_lifecycle(api_client, operations_csv):
    project = api_client.post("/api/projects", json={"name": "Operations"}).json()
    with operations_csv.open("rb") as source:
        dataset = api_client.post(
            f"/api/projects/{project['id']}/datasets",
            files={"file": ("operations.csv", source, "text/csv")},
        ).json()
    task = api_client.post(
        f"/api/projects/{project['id']}/tasks",
        json={"objective": "Why did East profit decline?"},
    ).json()
    assert dataset["artifact_type"] == "source_dataset"
    assert task["status"] == "understanding"
    assert api_client.get(f"/api/tasks/{task['id']}").status_code == 200
```

- [ ] **Step 2: Write SSE replay test**

```python
def test_events_endpoint_replays_after_sequence(api_client, task_repository, task):
    task_repository.append_event(task.id, "task.started", {})
    task_repository.append_event(task.id, "plan.updated", {"revision": 1})
    response = api_client.get(f"/api/tasks/{task.id}/events?after=1")
    assert "id: 2" in response.text
    assert "event: plan.updated" in response.text
    assert "id: 1" not in response.text
```

- [ ] **Step 3: Run API tests**

Run: `python -m pytest tests/test_api.py -v`

Expected: FAIL because the API app is missing.

- [ ] **Step 4: Implement HTTP resources and SSE stream**

Use explicit response models. File upload writes first to a temporary file inside the configured data root, then imports it through `DatasetService`. Task execution starts in a managed background thread so the request returns immediately. SSE replays persisted events after the supplied sequence and then polls for new events until client disconnect, sending a comment heartbeat every 15 seconds.

- [ ] **Step 5: Run API tests**

Run: `python -m pytest tests/test_api.py -v`

Expected: PASS.

- [ ] **Step 6: Commit the local API**

```powershell
git add src/data_analysis_agent/api.py src/data_analysis_agent/cli.py tests/test_api.py
git commit -m "feat: expose local analysis api"
```

### Task 9: Minimal Three-Area Task Workbench

**Files:**
- Create: `web/package.json`
- Create: `web/vite.config.ts`
- Create: `web/tsconfig.json`
- Create: `web/index.html`
- Create: `web/src/main.tsx`
- Create: `web/src/types.ts`
- Create: `web/src/api.ts`
- Create: `web/src/App.tsx`
- Create: `web/src/App.test.tsx`
- Create: `web/src/components/ProjectNav.tsx`
- Create: `web/src/components/TaskWorkbench.tsx`
- Create: `web/src/components/Conversation.tsx`
- Create: `web/src/styles.css`

**Interfaces:**
- Consumes: API response and task-event contracts from Task 8.
- Produces: `ApiClient` methods `listProjects`, `createProject`, `uploadDataset`, `listTasks`, `createTask`, `getTask`, `sendMessage`, and `subscribeToTask`.
- Produces: a responsive workbench with project navigation, task plan/timeline/artifacts, and conversation.

- [ ] **Step 1: Write the workbench behavior test**

```tsx
it("keeps conversation separate from durable task artifacts", async () => {
  render(<App api={fakeApiWithCompletedTask()} />);
  expect(await screen.findByRole("heading", { name: "East profit diagnosis" })).toBeVisible();
  expect(screen.getByRole("heading", { name: "Analysis plan" })).toBeVisible();
  expect(screen.getByRole("heading", { name: "Artifacts" })).toBeVisible();
  expect(screen.getByRole("textbox", { name: "Message the analyst" })).toBeVisible();
  expect(screen.getByRole("link", { name: "Logistics cost trend" })).toHaveAttribute(
    "href",
    expect.stringContaining("/api/artifacts/")
  );
});
```

- [ ] **Step 2: Run the frontend test**

Run: `npm --prefix web test -- --run`

Expected: FAIL because the frontend does not exist.

- [ ] **Step 3: Implement strict API types and client**

Define discriminated `TaskEvent` unions for `task.state`, `plan.updated`, `tool.started`, `tool.completed`, `artifact.created`, `approval.requested`, and `message.created`. The SSE client tracks the last sequence and reconnects with `after=<sequence>`.

- [ ] **Step 4: Implement the workbench components**

`ProjectNav` owns project/dataset/task selection. `TaskWorkbench` owns objective, plan, event timeline, artifacts, and report preview. `Conversation` owns message history, input, send state, and approval cards. `App` coordinates selection and incremental event updates without putting artifact content inside chat messages.

Use CSS grid for a desktop three-area layout and collapse project navigation above the workbench on narrow screens. Use a neutral analytical palette, tabular numerals, visible execution states, and no decorative gradients.

- [ ] **Step 5: Run frontend checks**

Run: `npm --prefix web test -- --run`

Expected: PASS.

Run: `npm --prefix web run build`

Expected: PASS with generated assets in `web/dist`.

- [ ] **Step 6: Commit the workbench**

```powershell
git add web
git commit -m "feat: add analysis task workbench"
```

### Task 10: OpenAI-Compatible Provider Adapter

**Files:**
- Modify: `src/data_analysis_agent/providers.py`
- Modify: `src/data_analysis_agent/settings.py`
- Create: `tests/test_providers.py`

**Interfaces:**
- Consumes: provider-neutral `ModelRequest`, `ModelResponse`, `ToolCall`, and `ToolResult`.
- Produces: `OpenAICompatibleProvider(base_url: str, api_key: str, model: str, timeout_seconds: int)`.
- Produces: `provider_from_settings(settings: Settings) -> ModelProvider`.

- [ ] **Step 1: Write response-normalization tests with a fake HTTP transport**

```python
def test_openai_adapter_normalizes_tool_call(fake_openai_transport):
    fake_openai_transport.respond_with_tool_call(
        call_id="call_1", name="inspect_dataset", arguments={"artifact_id": "a1"}
    )
    response = OpenAICompatibleProvider(
        base_url="https://model.invalid/v1",
        api_key="secret",
        model="test-model",
        transport=fake_openai_transport,
    ).complete(sample_model_request())
    assert response.tool_calls == (
        ToolCall(id="call_1", name="inspect_dataset", arguments={"artifact_id": "a1"}),
    )
```

- [ ] **Step 2: Run provider tests**

Run: `python -m pytest tests/test_providers.py -v`

Expected: FAIL because the adapter is missing.

- [ ] **Step 3: Implement HTTP request and normalization**

Send only normalized messages and JSON tool schemas. Parse assistant text and tool calls into domain records. Treat timeouts, rate limits, malformed tool arguments, authentication failure, and upstream server errors as distinct `ProviderError` categories. Never log the API key or authorization header.

- [ ] **Step 4: Run provider and agent tests**

Run: `python -m pytest tests/test_providers.py tests/test_agent.py -v`

Expected: PASS.

- [ ] **Step 5: Commit provider support**

```powershell
git add src/data_analysis_agent/providers.py src/data_analysis_agent/settings.py tests/test_providers.py
git commit -m "feat: connect openai compatible models"
```

### Task 11: End-to-End Acceptance Flow and Operating Documentation

**Files:**
- Create: `tests/test_acceptance.py`
- Create: `README.md`
- Modify: `pyproject.toml`
- Modify: `src/data_analysis_agent/api.py`
- Delete: `main.py`

**Interfaces:**
- Consumes: all backend and frontend interfaces.
- Produces: install command `python -m pip install -e ".[dev]"`.
- Produces: API command `data-analysis-agent serve --host 127.0.0.1 --port 8765`.
- Produces: deterministic acceptance command `python -m pytest tests/test_acceptance.py -v`.

- [ ] **Step 1: Write the complete deterministic acceptance test**

```python
def test_operating_diagnosis_creates_traceable_report(app_services, operations_csv):
    project = app_services.projects.create("Operating review")
    dataset = app_services.datasets.add_file(project.id, operations_csv)
    task = app_services.tasks.create(project.id, "Why did East profit decline?")
    provider = scripted_operating_diagnosis_provider(dataset.id)

    completed = app_services.agent(provider).run(task.id)

    assert completed.status is TaskStatus.COMPLETED
    artifacts = app_services.artifacts.list_for_task(task.id)
    assert {artifact.artifact_type for artifact in artifacts} >= {
        ArtifactType.PYTHON_CODE,
        ArtifactType.DERIVED_DATASET,
        ArtifactType.CHART,
        ArtifactType.MARKDOWN_REPORT,
    }
    report = next(a for a in artifacts if a.artifact_type is ArtifactType.MARKDOWN_REPORT)
    markdown = report.path.read_text(encoding="utf-8")
    assert "East" in markdown
    assert "artifact://" in markdown
    assert app_services.artifacts.evidence_for(report.id)
```

- [ ] **Step 2: Run the acceptance test and close integration gaps**

Run: `python -m pytest tests/test_acceptance.py -v`

Expected before integration fixes: FAIL at the first incomplete cross-module contract.

Implement only the adapters required to make the already-defined interfaces cooperate; do not add new product features.

- [ ] **Step 3: Document setup, trust boundaries, and the golden workflow**

The README must include Windows PowerShell commands for Python and frontend installation, API and UI startup, environment variables `DAA_MODEL_BASE_URL`, `DAA_MODEL_API_KEY`, and `DAA_MODEL_NAME`, the locally trusted Python-execution warning, project directory structure, deterministic test commands, and the operating-diagnosis walkthrough.

- [ ] **Step 4: Remove the generated sample entry point**

Delete the existing PyCharm sample `main.py`; `data-analysis-agent` is the only backend entry point.

- [ ] **Step 5: Run complete verification**

Run: `python -m pytest -v`

Expected: all backend tests PASS without a model API key.

Run: `npm --prefix web test -- --run`

Expected: all frontend tests PASS.

Run: `npm --prefix web run build`

Expected: production frontend build PASS.

Run: `git diff --check`

Expected: no whitespace errors.

- [ ] **Step 6: Commit the completed MVP**

```powershell
git add README.md pyproject.toml src tests web main.py
git commit -m "feat: complete traceable analysis MVP"
```

## Plan Self-Review Results

- Every MVP acceptance criterion in the design maps to Tasks 1 through 11.
- Database connection, cloud collaboration, scheduling, multi-agent work, Office export, and a notebook editor remain excluded.
- Domain names and signatures are consistent across repository, tool, runtime, API, and frontend tasks.
- All implementation tasks contain a failing test, an expected failure, a concrete implementation action, passing verification, and a commit boundary.
- No real model credential is required for deterministic unit, integration, or acceptance tests.
