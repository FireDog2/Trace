from __future__ import annotations

import sqlite3
from threading import Event

import pytest
from fastapi.testclient import TestClient
from handoff_samples import blocked_handoff

import data_analysis_agent.api as api_module
from data_analysis_agent.api import create_app
from data_analysis_agent.domain import PlanStepDraft, TaskStatus
from data_analysis_agent.model_config import SecretStorageUnavailable
from data_analysis_agent.permissions import ToolAction
from data_analysis_agent.providers import ModelResponse, ScriptedProvider
from data_analysis_agent.reporting import ReportService
from data_analysis_agent.scope import GLOBAL_PROJECT_ID
from data_analysis_agent.team import AgentRole, AgentRunStatus
from data_analysis_agent.tools import ToolCall, ToolContext
from data_analysis_agent.tracing import TraceContext


def test_project_can_be_renamed_and_global_workspace_stays_hidden(settings) -> None:
    app = create_app(settings, ScriptedProvider(()))

    with TestClient(app) as client:
        created = client.post("/api/projects", json={"name": "Monthly review"})
        updated = client.put(
            f"/api/projects/{created.json()['id']}",
            json={"name": "Quarterly review"},
        )
        projects = client.get("/api/projects")

    assert created.status_code == 201
    assert updated.status_code == 200
    assert updated.json()["name"] == "Quarterly review"
    assert [project["name"] for project in projects.json()] == ["Quarterly review"]
    assert all(project["id"] != GLOBAL_PROJECT_ID for project in projects.json())


def test_project_can_be_permanently_deleted_with_its_workspace(settings, operations_csv) -> None:
    app = create_app(
        settings,
        ScriptedProvider(()),
        secret_protector=RecordingProtector(),
    )

    with TestClient(app) as client:
        project = client.post("/api/projects", json={"name": "Temporary review"}).json()
        project_root = app.state.services.workspace.project_root(project["id"])
        other_project = app.state.services.projects.create("Other review")
        app.state.services.workspace.create_project(other_project.id)
        other_task = app.state.services.tasks.create(other_project.id, "Cross-project audit")
        legacy_source = app.state.services.data_sources.create(
            project["id"],
            "Legacy warehouse",
            "postgresql://localhost/review",
        )
        legacy_entry = app.state.services.knowledge.create(
            project["id"],
            kind="term",
            name="Legacy term",
            definition="A project-scoped definition from an older release.",
        )
        app.state.services.connection.execute(
            "INSERT INTO database_query_attempts("
            "id, task_id, data_source_id, policy_version, sql_hash, approval_id, "
            "status, error_category, started_at, completed_at"
            ") VALUES (?, ?, ?, ?, ?, NULL, 'completed', NULL, ?, ?)",
            (
                "cross-project-attempt",
                other_task.id,
                legacy_source.id,
                legacy_source.policy_version,
                "sql-hash",
                "2026-08-30T00:00:00+00:00",
                "2026-08-30T00:00:01+00:00",
            ),
        )
        app.state.services.connection.commit()
        with operations_csv.open("rb") as source:
            uploaded = client.post(
                f"/api/projects/{project['id']}/datasets",
                files={"file": ("operations.csv", source, "text/csv")},
            )
        task = app.state.services.tasks.create(project["id"], "Completed review")
        app.state.services.tasks.add_message(task.id, "user", "Review the uploaded data")
        app.state.services.tasks.revise_plan(
            task.id,
            (PlanStepDraft(title="Inspect the dataset"),),
            "Initial plan",
        )
        app.state.services.tasks.append_event(task.id, "task.started", {})
        for target in (
            TaskStatus.PLANNING,
            TaskStatus.RUNNING,
            TaskStatus.SYNTHESIZING,
            TaskStatus.COMPLETED,
        ):
            app.state.services.tasks.transition(task.id, target)
        deleted = client.delete(f"/api/projects/{project['id']}")

        assert uploaded.status_code == 201
        assert deleted.status_code == 204
        assert [item["id"] for item in client.get("/api/projects").json()] == [other_project.id]
        assert client.get(f"/api/tasks/{task.id}").status_code == 404
        assert client.get(f"/api/tasks/{other_task.id}").status_code == 200
        assert not project_root.exists()
        for table in ("artifacts", "tasks"):
            assert (
                app.state.services.connection.execute(
                    f"SELECT 1 FROM {table} WHERE project_id = ?",
                    (project["id"],),
                ).fetchone()
                is None
            )
        for table in ("messages", "plan_revisions", "task_events"):
            assert (
                app.state.services.connection.execute(
                    f"SELECT 1 FROM {table} WHERE task_id = ?",
                    (task.id,),
                ).fetchone()
                is None
            )
        assert (
            app.state.services.data_sources.get_public(legacy_source.id).project_id
            == GLOBAL_PROJECT_ID
        )
        assert app.state.services.knowledge.get(legacy_entry.id).project_id == GLOBAL_PROJECT_ID
        assert (
            app.state.services.connection.execute(
                "SELECT 1 FROM database_query_attempts WHERE id = 'cross-project-attempt'"
            ).fetchone()
            is not None
        )


def test_project_with_active_task_cannot_be_deleted(api_client) -> None:
    project = api_client.post("/api/projects", json={"name": "Active review"}).json()
    task = api_client.app.state.services.tasks.create(project["id"], "Still running")

    response = api_client.delete(f"/api/projects/{project['id']}")

    assert response.status_code == 409
    assert api_client.get(f"/api/tasks/{task.id}").status_code == 200
    assert api_client.app.state.services.workspace.project_root(project["id"]).exists()


def test_global_data_sources_and_knowledge_are_not_bound_to_a_user_project(
    settings,
) -> None:
    app = create_app(
        settings,
        ScriptedProvider(()),
        secret_protector=RecordingProtector(),
    )
    services = app.state.services
    first = services.projects.create("First project")
    second = services.projects.create("Second project")
    legacy_source = services.data_sources.create(
        first.id,
        "Legacy warehouse",
        "postgresql://reader@localhost/legacy",
    )
    services.knowledge.create(
        second.id,
        kind="term",
        name="Legacy term",
        definition="A previously project-scoped definition.",
    )

    with TestClient(app) as client:
        source = client.post(
            "/api/data-sources",
            json={
                "name": "Shared warehouse",
                "dsn": "postgresql://reader@localhost/shared",
            },
        )
        knowledge = client.post(
            "/api/knowledge",
            json={
                "kind": "metric",
                "name": "Shared profit",
                "definition": "Revenue minus cost.",
            },
        )
        sources = client.get("/api/data-sources")
        entries = client.get("/api/knowledge")
        search_result = services.knowledge.search_global("profit")

    assert source.status_code == 201 and source.json()["project_id"] == GLOBAL_PROJECT_ID
    assert knowledge.status_code == 201 and knowledge.json()["project_id"] == GLOBAL_PROJECT_ID
    assert {item["id"] for item in sources.json()} == {
        legacy_source.id,
        source.json()["id"],
    }
    assert {item["name"] for item in entries.json()} == {"Legacy term", "Shared profit"}
    assert search_result[0].name == "Shared profit"


def test_build_services_closes_open_connection_when_construction_fails(
    settings, monkeypatch
) -> None:
    class Connection:
        closed = False

        def close(self):
            self.closed = True

    connection = Connection()
    monkeypatch.setattr(api_module, "connect_database", lambda _: connection)
    monkeypatch.setattr(
        api_module,
        "_build_services",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("builder failed")),
    )

    with pytest.raises(RuntimeError, match="builder failed"):
        api_module.build_services(settings)
    assert connection.closed is True


def test_trace_routes_replay_without_execution_and_accept_completed_run(
    settings,
) -> None:
    skill = """---
name: safe-sql-diagnostic
description: Safe workflow.
---
## When to use
For analysis.
## Inputs
A question.
## Workflow
Analyze safely.
## Safety gates
Keep approvals.
## Failure recovery
Return a safe summary.
"""
    app = create_app(settings, ScriptedProvider([ModelResponse(skill, ())]))
    services = app.state.services
    project = services.projects.create("Trace route")
    task = services.tasks.create(project.id, "Replay safely")
    run = services.traces.start_run(task.id)
    services.trace_recorder.event(
        TraceContext(run.id, task.id),
        "tool.completed",
        "completed",
        {"dsn": "postgresql://secret", "row_count": 3},
    )
    services.approvals.request(
        task.id,
        ToolAction("query_database", {"dsn": "postgresql://analyst:secret@db"}),
        "EXPLAIN cost requires review",
    )
    checkpoint = services.context_checkpoints.create(
        owner_type="task",
        owner_id=task.id,
        through_message_id="message-1",
        summary="secret transcript detail",
        source_message_count=3,
        before_tokens=40_000,
        after_tokens=12_000,
        reason="auto",
    )
    services.traces.finish_run(run.id, "completed")
    with TestClient(app) as client:
        replay = client.get(f"/api/runs/{run.id}/trace")
        accepted = client.post(f"/api/runs/{run.id}/accept")
        accepted_again = client.post(f"/api/runs/{run.id}/accept")
        runs = client.get(f"/api/tasks/{task.id}/runs")
        curator_runs = services.team_repository.list_runs(task.id)
        assert len(curator_runs) == 1
        services.team_manager.await_runs((curator_runs[0].id,), 2)  # type: ignore[union-attr]
        curator = services.team_repository.list_runs(task.id)[0]
        candidate = services.skill_candidates.get_for_run(run.id)
        bundle = client.get(f"/api/runs/{run.id}/replay")
    assert replay.status_code == 200 and replay.json()[0]["sequence"] == 1
    assert "secret" not in replay.text
    assert accepted.status_code == 200 and accepted.json()["accepted"] is True
    assert accepted_again.status_code == 200
    assert runs.json()[0]["id"] == run.id
    assert curator.role is AgentRole.CURATOR
    assert curator.status is AgentRunStatus.COMPLETED
    assert candidate.enabled is False
    assert bundle.status_code == 200
    assert bundle.json()["integrity"]["sequence_contiguous"] is True
    assert bundle.json()["schema_version"] == 2
    assert bundle.json()["integrity"]["context_checkpoint_count"] == 1
    assert bundle.json()["context_checkpoints"][0]["id"] == checkpoint.id
    assert "summary" not in bundle.json()["context_checkpoints"][0]
    assert bundle.json()["skill_candidate"]["id"] == candidate.id
    assert bundle.json()["approvals"][0]["arguments"]["dsn"] == "[REDACTED]"
    assert "secret" not in bundle.text


def test_skill_candidate_can_be_loaded_by_source_run(settings) -> None:
    app = create_app(settings, ScriptedProvider(()))
    services = app.state.services
    project = services.projects.create("Skill lookup")
    task = services.tasks.create(project.id, "Replay candidate")
    run = services.traces.start_run(task.id)
    services.traces.finish_run(run.id, "completed")
    candidate = services.skill_candidates.create(
        run.id,
        "safe-sql-diagnostic",
        "---\nname: safe-sql-diagnostic\n---\n",
        "scripted",
    )

    with TestClient(app) as client:
        response = client.get(f"/api/runs/{run.id}/skill-candidate")

    assert response.status_code == 200
    assert response.json()["id"] == candidate.id


def test_markdown_report_can_be_exported_as_source_or_pdf(settings) -> None:
    app = create_app(settings, ScriptedProvider(()))
    services = app.state.services
    project = services.projects.create("PDF export")
    services.workspace.create_project(project.id)
    task = services.tasks.create(project.id, "华东利润诊断")
    report = ReportService(services.workspace, services.artifacts, services.tasks).publish(
        task.id,
        "# 分析报告\n\n**利润下降** 12%。\n\n## 原因\n\n- 物流成本上升\n- 收入稳定",
    )

    with TestClient(app) as client:
        download = client.get(f"/api/artifacts/{report.id}/content")
        preview = client.get(f"/api/artifacts/{report.id}/content?preview=true")
        markdown = client.get(f"/api/artifacts/{report.id}/export?format=markdown")
        pdf = client.get(f"/api/artifacts/{report.id}/export?format=pdf")

    assert download.headers["content-disposition"].startswith("attachment;")
    assert preview.headers["content-disposition"].startswith("inline;")
    assert preview.headers["content-security-policy"].startswith("sandbox allow-scripts;")
    assert preview.headers["x-content-type-options"] == "nosniff"
    assert markdown.status_code == 200
    assert markdown.content.startswith("# 分析报告".encode())
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.headers["content-disposition"].endswith('.pdf"')
    assert pdf.content.startswith(b"%PDF-")
    assert len(pdf.content) > 10_000


def test_business_knowledge_document_import_creates_classified_entries(
    settings,
) -> None:
    app = create_app(settings, ScriptedProvider(()))
    services = app.state.services
    project = services.projects.create("Knowledge import")
    document = """# 经营利润
定义：收入减去全部经营成本。
公式：revenue - operating_cost

# 数据导出规则
定义：敏感字段必须审批，不得直接导出。
""".encode()

    with TestClient(app) as client:
        response = client.post(
            f"/api/projects/{project.id}/knowledge/import",
            files={"file": ("财务口径.md", document, "text/markdown")},
        )

    assert response.status_code == 201
    assert response.json()["category_counts"] == {"metric": 1, "term": 0, "rule": 1}
    assert [entry["name"] for entry in response.json()["created"]] == [
        "经营利润",
        "数据导出规则",
    ]
    assert all(
        entry["source"] == "Document import · 财务口径.md" for entry in response.json()["created"]
    )


def test_policy_update_rejects_a_stale_editor_version(settings) -> None:
    app = create_app(
        settings,
        ScriptedProvider(()),
        secret_protector=RecordingProtector(),
    )
    services = app.state.services
    project = services.projects.create("Policy editing")
    source = services.data_sources.create(
        project.id, "Warehouse", "postgresql://readonly@localhost/warehouse"
    )

    with TestClient(app) as client:
        first = client.put(
            f"/api/data-sources/{source.id}/policies",
            json={
                "expected_version": 1,
                "rules": [{"table": "orders", "column": None, "access": "allowed"}],
            },
        )
        stale = client.put(
            f"/api/data-sources/{source.id}/policies",
            json={
                "expected_version": 1,
                "rules": [{"table": "orders", "column": None, "access": "denied"}],
            },
        )

    assert first.status_code == 200 and first.json()["version"] == 2
    assert stale.status_code == 409


@pytest.mark.parametrize("failure", ("provider", "runner_constructor", "runner_run"))
def test_worker_runner_factory_closes_fresh_worker_on_each_failure(
    settings, monkeypatch, failure
) -> None:
    main = api_module.build_services(settings, ScriptedProvider(()))
    project = main.projects.create("Worker lifecycle")
    # Create the task through the real main services; workers must never close this connection.
    task = main.tasks.create(project.id, "Probe")
    run = main.team_repository.create_run(task.id, AgentRole.SQL, "Probe")

    class WorkerConnection:
        closed = False

        def close(self):
            self.closed = True

    worker_connection = WorkerConnection()

    class Worker:
        connection = worker_connection
        team_repository = main.team_repository
        tools = main.tools

        def resolve_provider(self):
            if failure == "provider":
                raise RuntimeError("provider failed")
            return ScriptedProvider(())

    monkeypatch.setattr(api_module, "build_services", lambda *args, **kwargs: Worker())
    if failure == "runner_constructor":
        monkeypatch.setattr(
            api_module,
            "TeammateRunner",
            lambda **kwargs: (_ for _ in ()).throw(RuntimeError("ctor failed")),
        )
    elif failure == "runner_run":

        class FailingRunner:
            def __init__(self, **kwargs):
                pass

            def run(self, run_id):
                raise RuntimeError("run failed")

        monkeypatch.setattr(api_module, "TeammateRunner", FailingRunner)

    runner = main.team_manager._runner_factory(AgentRole.SQL, Event())  # type: ignore[union-attr]
    with pytest.raises(RuntimeError):
        runner.run(run.id)
    assert worker_connection.closed is True
    assert main.connection.execute("SELECT 1").fetchone()[0] == 1
    main.team_manager.shutdown()  # type: ignore[union-attr]
    main.connection.close()


def test_production_worker_builder_failure_closes_its_real_fresh_connection(
    settings, monkeypatch
) -> None:
    """The API's actual worker factory owns a connection even before services exist."""
    main = api_module.build_services(settings, ScriptedProvider(()))
    opened: list[sqlite3.Connection] = []
    real_connect = api_module.connect_database

    def recording_connect(worker_settings):
        connection = real_connect(worker_settings)
        opened.append(connection)
        return connection

    monkeypatch.setattr(api_module, "connect_database", recording_connect)
    monkeypatch.setattr(
        api_module,
        "_build_services",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("worker assembly failed")),
    )
    runner = main.team_manager._runner_factory(AgentRole.SQL, Event())  # type: ignore[union-attr]

    with pytest.raises(RuntimeError, match="worker assembly failed"):
        runner.run("unused-run")

    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute("SELECT 1")
    assert main.connection.execute("SELECT 1").fetchone()[0] == 1
    main.team_manager.shutdown()  # type: ignore[union-attr]
    main.connection.close()


@pytest.mark.parametrize("failure", ("get_run", "fail_run"))
def test_production_worker_failure_handler_closes_fresh_connection_on_repository_error(
    settings, monkeypatch, failure
) -> None:
    main = api_module.build_services(settings, ScriptedProvider(()))
    opened: list[sqlite3.Connection] = []
    real_connect = api_module.connect_database

    def recording_connect(worker_settings):
        connection = real_connect(worker_settings)
        opened.append(connection)
        return connection

    monkeypatch.setattr(api_module, "connect_database", recording_connect)
    if failure == "get_run":
        monkeypatch.setattr(
            api_module.TeamRepository,
            "get_run",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("read failed")),
        )
    else:

        class ActiveRun:
            status = AgentRunStatus.CREATED

        monkeypatch.setattr(
            api_module.TeamRepository, "get_run", lambda *args, **kwargs: ActiveRun()
        )
        monkeypatch.setattr(
            api_module.TeamRepository,
            "fail_run",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("write failed")),
        )

    with pytest.raises(RuntimeError):
        main.team_manager._worker_failure_handler("unused-run")  # type: ignore[union-attr]

    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute("SELECT 1")
    assert main.connection.execute("SELECT 1").fetchone()[0] == 1
    main.team_manager.shutdown()  # type: ignore[union-attr]
    main.connection.close()


@pytest.mark.parametrize(
    "helper,arguments,method",
    [
        ("_run_task", ("task-1",), "run"),
        ("_continue_task", ("task-1", "follow up", TaskStatus.COMPLETED), "follow_up"),
        ("_continue_task", ("task-1", "retry", TaskStatus.FAILED), "retry"),
        ("_resume_approved_task", ("task-1",), "resume_approval"),
    ],
)
def test_background_helpers_shutdown_teammates_before_closing_connection(
    settings, monkeypatch, helper, arguments, method
) -> None:
    events: list[str] = []

    class Connection:
        def close(self):
            events.append("connection.close")

    class Manager:
        class Run:
            id = "waiting-agent"

        def resume_waiting(self, task_id):
            events.append(f"manager.resume_waiting:{task_id}")
            return (self.Run(),)

        def await_runs(self, run_ids, timeout_seconds):
            events.append(f"manager.await_runs:{run_ids}:{timeout_seconds}")
            return ()

        def shutdown(self):
            events.append("manager.shutdown")

    class Agent:
        def __getattr__(self, name):
            if name != method:
                raise AttributeError(name)

            def invoke(*args):
                if name == "resume_approval" and len(args) > 1 and args[1] is not None:
                    args[1]()
                events.append(f"agent.{name}")

            return invoke

    class Services:
        connection = Connection()
        team_manager = Manager()

        def agent(self):
            return Agent()

    monkeypatch.setattr(api_module, "build_services", lambda *args, **kwargs: Services())
    getattr(api_module, helper)(settings, ScriptedProvider(()), *arguments)

    expected = [f"agent.{method}", "manager.shutdown", "connection.close"]
    if helper == "_resume_approved_task":
        expected.insert(0, "manager.resume_waiting:task-1")
        expected.insert(1, "manager.await_runs:('waiting-agent',):120")
    assert events == expected


def test_production_lead_delegates_database_and_artifact_work_to_teammates(
    settings,
) -> None:
    protector = RecordingProtector()
    lead = api_module.build_services(settings, ScriptedProvider(()), protector)
    try:
        project = lead.projects.create("Role boundary")
        task = lead.tasks.create(project.id, "No direct SQL")
        lead_names = {schema.name for schema in lead.tools.schemas()}
        assert "query_database" not in lead_names
        assert "search_business_knowledge" not in lead_names
        assert not {"run_python", "save_artifact", "write_report"} & lead_names
        assert {"spawn_teammate", "list_teammates", "await_teammates"} <= lead_names
        rejected = lead.tools.execute(
            ToolCall(
                "lead-query",
                "query_database",
                {"data_source_id": "source", "sql": "SELECT 1"},
            ),
            ToolContext(project.id, task.id),
        )
        assert rejected.error_category == "unknown_tool"

        worker = api_module.build_services(
            settings,
            secret_protector=protector,
            with_team_manager=False,
        )
        try:
            worker_names = {schema.name for schema in worker.tools.schemas()}
            assert {
                "query_database",
                "search_business_knowledge",
                "run_python",
                "save_artifact",
                "write_report",
            } <= worker_names
            lead.knowledge.create(
                project.id,
                kind="metric",
                name="Gross profit",
                definition="Revenue minus cost of goods sold.",
                formula="revenue - cogs",
            )
            result = worker.tools.execute(
                ToolCall(
                    "knowledge-search",
                    "search_business_knowledge",
                    {"query": "gross profit"},
                ),
                ToolContext(project.id, task.id),
            )
            assert result.success
            assert result.output["entries"][0]["formula"] == "revenue - cogs"
        finally:
            worker.connection.close()
    finally:
        lead.team_manager.shutdown()  # type: ignore[union-attr]
        lead.connection.close()


def test_business_knowledge_api_crud_and_versioning(api_client) -> None:
    project = api_client.post("/api/projects", json={"name": "Metric catalog"}).json()
    payload = {
        "kind": "metric",
        "name": "Operating profit",
        "definition": "Revenue minus operating costs.",
        "formula": "revenue - operating_cost",
        "dimensions": ["month", "region"],
        "synonyms": ["Operating income", "EBIT"],
        "source": "Finance handbook",
    }

    created_response = api_client.post(f"/api/projects/{project['id']}/knowledge", json=payload)
    assert created_response.status_code == 201
    created = created_response.json()
    assert created["version"] == 1

    listed = api_client.get(f"/api/projects/{project['id']}/knowledge").json()
    assert listed == [created]

    payload["definition"] = "Revenue minus all operating expenses."
    updated_response = api_client.put(f"/api/knowledge/{created['id']}", json=payload)
    assert updated_response.status_code == 200
    assert updated_response.json()["version"] == 2

    duplicate_response = api_client.post(f"/api/projects/{project['id']}/knowledge", json=payload)
    assert duplicate_response.status_code == 409

    deleted = api_client.delete(f"/api/knowledge/{created['id']}")
    assert deleted.status_code == 204
    assert api_client.get(f"/api/projects/{project['id']}/knowledge").json() == []


def test_explicit_teammate_provider_factory_creates_fresh_workers_without_model_config(
    settings,
) -> None:
    created: list[ScriptedProvider] = []

    def teammate_provider_factory() -> ScriptedProvider:
        class RoleProvider(ScriptedProvider):
            def complete(self, request):
                self.requests.append(request)
                role = "knowledge" if "[role.knowledge@" in request.system else "report"
                return ModelResponse(blocked_handoff(role, "done"), ())

        provider = RoleProvider([])
        created.append(provider)
        return provider

    app = create_app(
        settings,
        secret_protector=RecordingProtector(),
        teammate_provider_factory=teammate_provider_factory,
    )
    services = app.state.services
    try:
        project = services.projects.create("Factory")
        task = services.tasks.create(project.id, "Delegate")
        first = services.team_manager.spawn(task.id, AgentRole.KNOWLEDGE, "One")  # type: ignore[union-attr]
        second = services.team_manager.spawn(task.id, AgentRole.REPORT, "Two")  # type: ignore[union-attr]
        completed = services.team_manager.await_runs((first.id, second.id), 2)  # type: ignore[union-attr]
        assert {run.status for run in completed} == {AgentRunStatus.COMPLETED}
        assert len(created) == 2
        assert created[0] is not created[1]
    finally:
        services.team_manager.shutdown()  # type: ignore[union-attr]
        services.connection.close()


class RecordingProtector:
    def protect(self, value: str) -> bytes:
        return b"protected:" + value.encode("utf-8")

    def unprotect(self, value: bytes) -> str:
        return value.removeprefix(b"protected:").decode("utf-8")


class UnreadableProtector(RecordingProtector):
    def unprotect(self, value: bytes) -> str:
        raise SecretStorageUnavailable("Saved model key cannot be decrypted")


class UnavailableProtector(RecordingProtector):
    def protect(self, value: str) -> bytes:
        raise SecretStorageUnavailable("Secure storage is unavailable")


def test_project_dataset_task_lifecycle(api_client, operations_csv) -> None:
    project_response = api_client.post(
        "/api/projects",
        json={"name": "Operations"},
    )
    assert project_response.status_code == 201
    project = project_response.json()

    with operations_csv.open("rb") as source:
        dataset_response = api_client.post(
            f"/api/projects/{project['id']}/datasets",
            files={"file": ("operations.csv", source, "text/csv")},
        )
    assert dataset_response.status_code == 201
    dataset = dataset_response.json()

    task_response = api_client.post(
        f"/api/projects/{project['id']}/tasks",
        json={"objective": "Why did East profit decline?"},
    )
    assert task_response.status_code == 201
    task = task_response.json()

    assert dataset["artifact_type"] == "source_dataset"
    assert (
        api_client.get(f"/api/projects/{project['id']}/artifacts").json()[0]["title"]
        == "operations.csv"
    )
    assert task["status"] == "understanding"
    assert task["pinned"] is False
    assert api_client.get(f"/api/tasks/{task['id']}").status_code == 200
    assert (
        api_client.get(f"/api/artifacts/{dataset['id']}/dataset-inspection").json()["row_count"]
        == 12
    )


def test_task_can_be_pinned_and_completed_task_can_be_deleted(api_client) -> None:
    project = api_client.post("/api/projects", json={"name": "Operations"}).json()
    repository = api_client.app.state.services.tasks
    task = repository.create(project["id"], "Keep this conversation handy")

    pinned = api_client.put(
        f"/api/tasks/{task.id}/pin",
        json={"pinned": True},
    )

    assert pinned.status_code == 200
    assert pinned.json()["pinned"] is True
    for target in (
        TaskStatus.PLANNING,
        TaskStatus.RUNNING,
        TaskStatus.SYNTHESIZING,
        TaskStatus.COMPLETED,
    ):
        repository.transition(task.id, target)

    deleted = api_client.delete(f"/api/tasks/{task.id}")

    assert deleted.status_code == 204
    assert api_client.get(f"/api/tasks/{task.id}").status_code == 404
    assert api_client.get(f"/api/projects/{project['id']}/tasks").json() == []


def test_active_task_delete_requires_waiting_for_completion(api_client) -> None:
    project = api_client.post("/api/projects", json={"name": "Operations"}).json()
    task = api_client.app.state.services.tasks.create(project["id"], "Still running")

    response = api_client.delete(f"/api/tasks/{task.id}")

    assert response.status_code == 409


def test_events_endpoint_replays_after_sequence(api_client) -> None:
    project = api_client.post(
        "/api/projects",
        json={"name": "Operations"},
    ).json()
    task = api_client.post(
        f"/api/projects/{project['id']}/tasks",
        json={"objective": "Why did East profit decline?"},
    ).json()
    repository = api_client.app.state.services.tasks
    repository.append_event(task["id"], "task.started", {})
    repository.append_event(task["id"], "plan.updated", {"revision": 1})

    response = api_client.get(f"/api/tasks/{task['id']}/events?after=1")

    assert response.status_code == 200
    assert "id: 2" in response.text
    assert "event: plan.updated" in response.text
    assert "id: 1" not in response.text


def test_agents_endpoint_exposes_safe_state_and_lifecycle_events(settings) -> None:
    app = create_app(
        settings,
        teammate_provider_factory=lambda: ScriptedProvider(
            [
                ModelResponse(
                    text=blocked_handoff("sql", "Profit is revenue minus cost"), tool_calls=()
                )
            ]
        ),
    )
    services = app.state.services
    try:
        project = services.projects.create("Agent visibility")
        task = services.tasks.create(project.id, "Delegate a safe SQL summary")
        run = services.team_manager.spawn(task.id, AgentRole.SQL, "Aggregate profit")  # type: ignore[union-attr]
        services.team_manager.await_runs((run.id,), 2)  # type: ignore[union-attr]

        with TestClient(app) as client:
            response = client.get(f"/api/tasks/{task.id}/agents")
            events = client.get(f"/api/tasks/{task.id}/events").text

        assert response.status_code == 200
        assert response.json() == [
            {
                "id": run.id,
                "task_id": task.id,
                "role": "sql",
                "objective": "Aggregate profit",
                "handoff_validation": response.json()[0]["handoff_validation"],
                "work_order": response.json()[0]["work_order"],
                "status": "completed",
                "result_summary": "Profit is revenue minus cost",
                "error_category": None,
                "created_at": response.json()[0]["created_at"],
                "started_at": response.json()[0]["started_at"],
                "completed_at": response.json()[0]["completed_at"],
            }
        ]
        assert "messages" not in response.text
        assert "credentials" not in response.text
        assert "event: agent.created" in events
        assert "event: agent.state" in events
        assert "event: agent.message" in events
        assert "event: agent.completed" in events
        assert events.index("event: agent.created") < events.index("event: agent.completed")
        assert run.id in events
    finally:
        services.team_manager.shutdown()  # type: ignore[union-attr]
        services.connection.close()


def test_agents_endpoint_returns_not_found_for_unknown_task(api_client) -> None:
    response = api_client.get("/api/tasks/missing-task/agents")
    assert response.status_code == 404


def test_completed_task_accepts_follow_up_after_application_restart(settings) -> None:
    provider = ScriptedProvider(
        [
            ModelResponse(text="Initial diagnosis complete.", tool_calls=()),
            ModelResponse(text="Follow-up diagnosis complete.", tool_calls=()),
        ]
    )
    with TestClient(create_app(settings, provider)) as first_client:
        project = first_client.post(
            "/api/projects",
            json={"name": "Operating review"},
        ).json()
        task = first_client.post(
            f"/api/projects/{project['id']}/tasks",
            json={"objective": "Why did East profit decline?"},
        ).json()
        assert first_client.get(f"/api/tasks/{task['id']}").json()["status"] == "completed"

    with TestClient(create_app(settings, provider)) as restarted_client:
        response = restarted_client.post(
            f"/api/tasks/{task['id']}/messages",
            json={"content": "Now compare the East result with West."},
        )

        assert response.status_code == 200
        detail = restarted_client.get(f"/api/tasks/{task['id']}").json()
        assert detail["status"] == "completed"
        assert [message["content"] for message in detail["messages"]][-2:] == [
            "Now compare the East result with West.",
            "Follow-up diagnosis complete.",
        ]


def test_failed_task_accepts_a_retry_message(settings) -> None:
    provider = ScriptedProvider([ModelResponse(text="Recovered after retry.", tool_calls=())])
    app = create_app(settings, provider)
    services = app.state.services
    project = services.projects.create("Retry review")
    task = services.tasks.create(project.id, "List current artifacts")
    services.tasks.transition(task.id, TaskStatus.FAILED)

    with TestClient(app) as client:
        response = client.post(
            f"/api/tasks/{task.id}/messages",
            json={"content": "Retry the project inventory."},
        )
        detail = client.get(f"/api/tasks/{task.id}").json()

    assert response.status_code == 200
    assert detail["status"] == "completed"
    assert [message["content"] for message in detail["messages"]] == [
        "Retry the project inventory.",
        "Recovered after retry.",
    ]


def test_running_task_rejects_a_second_message(settings) -> None:
    app = create_app(settings, ScriptedProvider([]))
    services = app.state.services
    project = services.projects.create("Active review")
    task = services.tasks.create(project.id, "Analyze current performance")
    services.tasks.transition(task.id, TaskStatus.PLANNING)
    services.tasks.transition(task.id, TaskStatus.RUNNING)

    with TestClient(app) as client:
        response = client.post(
            f"/api/tasks/{task.id}/messages",
            json={"content": "Submit this again"},
        )
        detail = client.get(f"/api/tasks/{task.id}").json()

    assert response.status_code == 409
    assert response.json()["detail"].startswith("Task is already running")
    assert detail["messages"] == []


def test_approval_decision_is_visible_and_resumes_configured_agent(settings) -> None:
    provider = ScriptedProvider([ModelResponse(text="Approved action handled.", tool_calls=())])
    app = create_app(settings, provider)
    services = app.state.services
    project = services.projects.create("Approval review")
    task = services.tasks.create(project.id, "Use an external benchmark")
    services.tasks.transition(task.id, TaskStatus.NEEDS_INPUT)
    approval = services.approvals.request(
        task.id,
        ToolAction("network_access", {"url": "https://example.invalid"}),
        "External network access requires approval",
    )

    with TestClient(app) as client:
        response = client.post(
            f"/api/approvals/{approval.id}/decision",
            json={"approved": True},
        )

        assert response.status_code == 200
        assert client.get(f"/api/tasks/{task.id}").json()["status"] == "completed"
        events = client.get(f"/api/tasks/{task.id}/events").text
        assert "event: approval.decided" in events
        assert '"status": "approved"' in events


def test_approval_stays_pending_when_model_is_unconfigured(settings) -> None:
    app = create_app(settings, secret_protector=RecordingProtector())
    services = app.state.services
    project = services.projects.create("Approval review")
    task = services.tasks.create(project.id, "Use an external benchmark")
    services.tasks.transition(task.id, TaskStatus.NEEDS_INPUT)
    approval = services.approvals.request(
        task.id,
        ToolAction("network_access", {"url": "https://example.invalid"}),
        "External network access requires approval",
    )

    with TestClient(app) as client:
        response = client.post(
            f"/api/approvals/{approval.id}/decision",
            json={"approved": True},
        )

        assert response.status_code == 409
        assert services.approvals.get(approval.id).status == "pending"
        events = client.get(f"/api/tasks/{task.id}/events").text
        assert "event: approval.decided" not in events


def test_model_configuration_api_never_returns_key(settings) -> None:
    with TestClient(create_app(settings, secret_protector=RecordingProtector())) as client:
        response = client.put(
            "/api/model-config",
            json={
                "provider": "openai",
                "model": "gpt-5.6-terra",
                "api_key": "openai-secret",
            },
        )

        assert response.status_code == 200
        body = client.get("/api/model-config").json()
        assert body["provider"] == "openai"
        assert body["base_url"] == "https://api.openai.com/v1"
        assert body["has_api_key"] is True
        assert body["configured"] is True
        assert body["providers"][0]["provider"] == "deepseek"
        assert "api_key" not in body
        assert "openai-secret" not in client.get("/api/model-config").text


def test_model_configuration_api_preserves_blank_key_for_same_provider(
    settings,
) -> None:
    protector = RecordingProtector()
    app = create_app(settings, secret_protector=protector)
    with TestClient(app) as client:
        client.put(
            "/api/model-config",
            json={
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "api_key": "deepseek-secret",
            },
        )

        response = client.put(
            "/api/model-config",
            json={
                "provider": "deepseek",
                "model": "deepseek-v4-pro",
                "api_key": None,
            },
        )

        assert response.status_code == 200
        assert app.state.services.model_configs.credentials().api_key == "deepseek-secret"


def test_builtin_model_configuration_rejects_endpoint_override(settings) -> None:
    with TestClient(create_app(settings, secret_protector=RecordingProtector())) as client:
        response = client.put(
            "/api/model-config",
            json={
                "provider": "openai",
                "base_url": "https://relay.example/v1",
                "model": "gpt-5.6-terra",
                "api_key": "openai-secret",
            },
        )

        assert response.status_code == 422
        assert response.json() == {"detail": "Invalid model configuration"}


def test_unconfigured_task_is_rejected_before_creation(settings) -> None:
    with TestClient(create_app(settings, secret_protector=RecordingProtector())) as client:
        project = client.post("/api/projects", json={"name": "Review"}).json()

        response = client.post(
            f"/api/projects/{project['id']}/tasks",
            json={"objective": "Diagnose profit"},
        )

        assert response.status_code == 409
        assert response.json()["detail"]["category"] == "model_not_configured"
        assert client.get(f"/api/projects/{project['id']}/tasks").json() == []


def test_saved_configuration_is_resolved_for_a_new_task(settings) -> None:
    captured = []

    def provider_factory(credentials, timeout_seconds=60):
        captured.append((credentials, timeout_seconds))
        return ScriptedProvider([ModelResponse(text="Configured model completed.", tool_calls=())])

    with TestClient(
        create_app(
            settings,
            secret_protector=RecordingProtector(),
            provider_factory=provider_factory,
        )
    ) as client:
        client.put(
            "/api/model-config",
            json={
                "provider": "custom",
                "base_url": "https://relay.example/v1/",
                "model": "relay-model",
                "api_key": "relay-key",
            },
        )
        project = client.post("/api/projects", json={"name": "Review"}).json()

        task = client.post(
            f"/api/projects/{project['id']}/tasks",
            json={"objective": "Diagnose profit"},
        ).json()

        assert client.get(f"/api/tasks/{task['id']}").json()["status"] == "completed"
        credentials, timeout_seconds = captured[0]
        assert credentials.base_url == "https://relay.example/v1"
        assert credentials.model == "relay-model"
        assert credentials.api_key == "relay-key"
        assert timeout_seconds == settings.model_timeout_seconds


def test_latest_configuration_is_resolved_for_each_agent_run(settings) -> None:
    captured_models: list[str] = []

    def provider_factory(credentials, timeout_seconds=60):
        captured_models.append(credentials.model)
        return ScriptedProvider(
            [ModelResponse(text=f"Completed with {credentials.model}.", tool_calls=())]
        )

    app = create_app(
        settings,
        secret_protector=RecordingProtector(),
        provider_factory=provider_factory,
    )
    with TestClient(app) as client:
        client.put(
            "/api/model-config",
            json={
                "provider": "custom",
                "base_url": "https://relay.example/v1",
                "model": "relay-a",
                "api_key": "relay-key",
            },
        )
        project = client.post("/api/projects", json={"name": "Review"}).json()
        first_task = client.post(
            f"/api/projects/{project['id']}/tasks",
            json={"objective": "First diagnosis"},
        ).json()

        client.put(
            "/api/model-config",
            json={
                "provider": "openai",
                "model": "gpt-5.6-terra",
                "api_key": "openai-key",
            },
        )
        client.post(
            f"/api/projects/{project['id']}/tasks",
            json={"objective": "Second diagnosis"},
        )
        client.post(
            f"/api/tasks/{first_task['id']}/messages",
            json={"content": "Follow up with the latest model"},
        )

        services = app.state.services
        approval_task = services.tasks.create(project["id"], "Approved diagnosis")
        services.tasks.transition(approval_task.id, TaskStatus.NEEDS_INPUT)
        approval = services.approvals.request(
            approval_task.id,
            ToolAction("network_access", {"url": "https://example.invalid"}),
            "External network access requires approval",
        )
        response = client.post(
            f"/api/approvals/{approval.id}/decision",
            json={"approved": True},
        )

        assert response.status_code == 200
        assert captured_models == [
            "relay-a",
            "gpt-5.6-terra",
            "gpt-5.6-terra",
            "gpt-5.6-terra",
        ]


def test_unreadable_saved_key_rejects_task_before_creation(settings) -> None:
    with TestClient(create_app(settings, secret_protector=UnreadableProtector())) as client:
        client.put(
            "/api/model-config",
            json={
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "api_key": "deepseek-key",
            },
        )
        project = client.post("/api/projects", json={"name": "Review"}).json()

        response = client.post(
            f"/api/projects/{project['id']}/tasks",
            json={"objective": "Diagnose profit"},
        )

        assert response.status_code == 409
        assert client.get(f"/api/projects/{project['id']}/tasks").json() == []


def test_unavailable_secret_storage_returns_service_unavailable(settings) -> None:
    with TestClient(create_app(settings, secret_protector=UnavailableProtector())) as client:
        response = client.put(
            "/api/model-config",
            json={
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "api_key": "deepseek-key",
            },
        )

        assert response.status_code == 503
        assert response.json()["detail"] == "Secure storage is unavailable"


def test_secret_storage_error_never_reflects_secret(settings) -> None:
    secret = "postgresql://analyst:password@host/ciphertext"

    class SecretProtector:
        def protect(self, value: str) -> bytes:
            raise SecretStorageUnavailable(secret)

        def unprotect(self, value: bytes) -> str:
            return value.decode()

    with TestClient(create_app(settings, secret_protector=SecretProtector())) as client:
        response = client.put(
            "/api/model-config",
            json={"provider": "openai", "model": "gpt-5.6-terra", "api_key": "key"},
        )

    assert response.status_code == 503
    assert secret not in response.text
    assert response.json() == {"detail": "Secure storage is unavailable"}


def test_data_source_api_never_returns_dsn(settings) -> None:
    with TestClient(create_app(settings, secret_protector=RecordingProtector())) as client:
        project = client.post("/api/projects", json={"name": "Review"}).json()

        response = client.post(
            f"/api/projects/{project['id']}/data-sources",
            json={
                "name": "Warehouse",
                "dsn": "postgresql://analyst:secret@localhost/portfolio",
            },
        )

        assert response.status_code == 201
        assert "dsn" not in response.json()
        assert "secret" not in response.text
        assert client.get(f"/api/projects/{project['id']}/data-sources").json() == [response.json()]


def test_data_source_connection_can_be_updated_without_returning_saved_dsn(
    settings,
) -> None:
    app = create_app(settings, secret_protector=RecordingProtector())
    with TestClient(app) as client:
        project = client.post("/api/projects", json={"name": "Review"}).json()
        source = client.post(
            f"/api/projects/{project['id']}/data-sources",
            json={
                "name": "Warehouse",
                "dsn": "postgresql://reader:secret@localhost/review",
            },
        ).json()

        renamed = client.put(
            f"/api/data-sources/{source['id']}",
            json={"name": "Finance Warehouse"},
        )
        replaced = client.put(
            f"/api/data-sources/{source['id']}",
            json={
                "name": "Finance Warehouse",
                "dsn": "postgresql://reader:new-secret@localhost/finance",
            },
        )
        stored_dsn = app.state.services.data_sources.credentials(source["id"]).dsn

    assert renamed.status_code == 200 and renamed.json()["name"] == "Finance Warehouse"
    assert replaced.status_code == 200
    assert "dsn" not in renamed.json() and "dsn" not in replaced.json()
    assert "secret" not in renamed.text and "new-secret" not in replaced.text
    assert stored_dsn.endswith("/finance")


def test_data_source_can_be_deleted_globally_and_its_credentials_are_erased(
    settings,
) -> None:
    app = create_app(settings, secret_protector=RecordingProtector())
    with TestClient(app) as client:
        source = client.post(
            "/api/data-sources",
            json={
                "name": "Warehouse",
                "dsn": "postgresql://reader:secret@localhost/review",
            },
        ).json()

        deleted = client.delete(f"/api/data-sources/{source['id']}")
        listed = client.get("/api/data-sources")
        missing = client.get(f"/api/data-sources/{source['id']}/policies")
        repeated = client.delete(f"/api/data-sources/{source['id']}")
        stored = app.state.services.connection.execute(
            "SELECT protected_dsn, deleted_at FROM data_sources WHERE id = ?",
            (source["id"],),
        ).fetchone()

    assert deleted.status_code == 204
    assert listed.json() == []
    assert missing.status_code == 404
    assert repeated.status_code == 404
    assert bytes(stored["protected_dsn"]) == b""
    assert stored["deleted_at"] is not None


def test_data_source_policy_and_schema_apis_are_project_scoped(settings) -> None:
    app = create_app(settings, secret_protector=RecordingProtector())

    class SchemaDriver:
        def inspect_schema(self, dsn: str):
            assert "secret" not in dsn
            return {"orders": ("region", "amount")}

    app.state.services.sql_gateway.driver = SchemaDriver()
    with TestClient(app) as client:
        project = client.post("/api/projects", json={"name": "Review"}).json()
        source = client.post(
            f"/api/projects/{project['id']}/data-sources",
            json={"name": "Warehouse", "dsn": "postgresql://localhost/portfolio"},
        ).json()

        policy = client.put(
            f"/api/data-sources/{source['id']}/policies",
            json={"rules": [{"table": "orders", "column": None, "access": "allowed"}]},
        )

        assert policy.status_code == 200
        assert policy.json()["version"] == 2
        current_policy = client.get(f"/api/data-sources/{source['id']}/policies")
        assert current_policy.status_code == 200
        assert current_policy.json() == {
            "data_source_id": source["id"],
            "version": 2,
            "rules": [{"table": "orders", "column": None, "access": "allowed"}],
        }
        assert "postgresql://" not in current_policy.text
        schema = client.get(f"/api/data-sources/{source['id']}/schema")
        assert schema.status_code == 200
        assert schema.json()["tables"] == {"orders": ["region", "amount"]}
        assert "postgresql://" not in schema.text


def test_data_source_api_validation_never_reflects_sensitive_input(settings) -> None:
    secret = "postgresql://analyst:password@host/secret-db"
    with TestClient(create_app(settings, secret_protector=RecordingProtector())) as client:
        project = client.post("/api/projects", json={"name": "Review"}).json()
        response = client.post(
            f"/api/projects/{project['id']}/data-sources",
            json={"name": "Warehouse", "dsn": secret * 200, "password": secret},
        )

    assert response.status_code == 422
    assert secret not in response.text
    assert response.json() == {"detail": "Invalid request", "error_count": 2}


def test_api_validation_never_reflects_a_sensitive_extra_field_name(settings) -> None:
    secret_key = "postgresql://analyst:password@host/ciphertext"
    with TestClient(create_app(settings, secret_protector=RecordingProtector())) as client:
        project = client.post("/api/projects", json={"name": "Review"}).json()
        response = client.post(
            f"/api/projects/{project['id']}/data-sources",
            json={
                "name": "Warehouse",
                "dsn": "postgresql://localhost/review",
                secret_key: "x",
            },
        )

    assert response.status_code == 422
    assert secret_key not in response.text
    assert response.json() == {"detail": "Invalid request", "error_count": 1}


def test_model_configuration_error_never_reflects_model_input(settings) -> None:
    secret = "postgresql://analyst:password@host/ciphertext"
    with TestClient(create_app(settings, secret_protector=RecordingProtector())) as client:
        response = client.put(
            "/api/model-config",
            json={"provider": "openai", "model": secret, "api_key": "key"},
        )

    assert response.status_code == 422
    assert secret not in response.text
    assert response.json() == {"detail": "Invalid model configuration"}


def test_missing_data_source_uses_fixed_404_without_identifier(settings) -> None:
    hostile_id = "source-password-ciphertext"
    with TestClient(create_app(settings, secret_protector=RecordingProtector())) as client:
        for response in (
            client.put(f"/api/data-sources/{hostile_id}/policies", json={"rules": []}),
            client.get(f"/api/data-sources/{hostile_id}/policies"),
            client.get(f"/api/data-sources/{hostile_id}/schema"),
        ):
            assert response.status_code == 404
            assert response.json() == {"detail": "Data source not found"}
            assert hostile_id not in response.text
