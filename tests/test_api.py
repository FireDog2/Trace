from __future__ import annotations

import sqlite3
from threading import Event

import pytest
from fastapi.testclient import TestClient

import data_analysis_agent.api as api_module
from data_analysis_agent.api import create_app
from data_analysis_agent.domain import TaskStatus
from data_analysis_agent.model_config import SecretStorageUnavailable
from data_analysis_agent.permissions import ToolAction
from data_analysis_agent.providers import ModelResponse, ScriptedProvider
from data_analysis_agent.team import AgentRole, AgentRunStatus
from data_analysis_agent.tools import ToolCall, ToolContext


def test_build_services_closes_open_connection_when_construction_fails(settings, monkeypatch) -> None:
    class Connection:
        closed = False
        def close(self):
            self.closed = True

    connection = Connection()
    monkeypatch.setattr(api_module, "connect_database", lambda _: connection)
    monkeypatch.setattr(api_module, "_build_services", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("builder failed")))

    with pytest.raises(RuntimeError, match="builder failed"):
        api_module.build_services(settings)
    assert connection.closed is True


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
        monkeypatch.setattr(api_module, "TeammateRunner", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("ctor failed")))
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

        monkeypatch.setattr(api_module.TeamRepository, "get_run", lambda *args, **kwargs: ActiveRun())
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


@pytest.mark.parametrize("helper,arguments,method", [
    ("_run_task", ("task-1",), "run"),
    ("_continue_task", ("task-1", "follow up", TaskStatus.COMPLETED), "follow_up"),
    ("_resume_approved_task", ("task-1",), "resume_approval"),
])
def test_background_helpers_shutdown_teammates_before_closing_connection(
    settings, monkeypatch, helper, arguments, method
) -> None:
    events: list[str] = []

    class Connection:
        def close(self):
            events.append("connection.close")

    class Manager:
        def shutdown(self):
            events.append("manager.shutdown")

    class Agent:
        def __getattr__(self, name):
            if name != method:
                raise AttributeError(name)
            return lambda *args: events.append(f"agent.{name}")

    class Services:
        connection = Connection()
        team_manager = Manager()
        def agent(self):
            return Agent()

    monkeypatch.setattr(api_module, "build_services", lambda *args, **kwargs: Services())
    getattr(api_module, helper)(settings, ScriptedProvider(()), *arguments)

    assert events == [f"agent.{method}", "manager.shutdown", "connection.close"]


def test_production_lead_rejects_database_query_but_sql_worker_keeps_it(settings) -> None:
    protector = RecordingProtector()
    lead = api_module.build_services(settings, ScriptedProvider(()), protector)
    try:
        project = lead.projects.create("Role boundary")
        task = lead.tasks.create(project.id, "No direct SQL")
        lead_names = {schema.name for schema in lead.tools.schemas()}
        assert "query_database" not in lead_names
        rejected = lead.tools.execute(
            ToolCall("lead-query", "query_database", {"data_source_id": "source", "sql": "SELECT 1"}),
            ToolContext(project.id, task.id),
        )
        assert rejected.error_category == "unknown_tool"

        worker = api_module.build_services(
            settings, secret_protector=protector, with_team_manager=False,
        )
        try:
            assert "query_database" in {schema.name for schema in worker.tools.schemas()}
        finally:
            worker.connection.close()
    finally:
        lead.team_manager.shutdown()  # type: ignore[union-attr]
        lead.connection.close()


def test_explicit_teammate_provider_factory_creates_fresh_workers_without_model_config(settings) -> None:
    created: list[ScriptedProvider] = []

    def teammate_provider_factory() -> ScriptedProvider:
        provider = ScriptedProvider([ModelResponse('{"summary":"done"}', ())])
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
    assert api_client.get(
        f"/api/projects/{project['id']}/artifacts"
    ).json()[0]["title"] == "operations.csv"
    assert task["status"] == "understanding"
    assert api_client.get(f"/api/tasks/{task['id']}").status_code == 200
    assert api_client.get(
        f"/api/artifacts/{dataset['id']}/dataset-inspection"
    ).json()["row_count"] == 12


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


def test_approval_decision_is_visible_and_resumes_configured_agent(settings) -> None:
    provider = ScriptedProvider(
        [ModelResponse(text="Approved action handled.", tool_calls=())]
    )
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
    with TestClient(
        create_app(settings, secret_protector=RecordingProtector())
    ) as client:
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


def test_model_configuration_api_preserves_blank_key_for_same_provider(settings) -> None:
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
    with TestClient(
        create_app(settings, secret_protector=RecordingProtector())
    ) as client:
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
    with TestClient(
        create_app(settings, secret_protector=RecordingProtector())
    ) as client:
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
        return ScriptedProvider(
            [ModelResponse(text="Configured model completed.", tool_calls=())]
        )

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
    with TestClient(
        create_app(settings, secret_protector=UnreadableProtector())
    ) as client:
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
    with TestClient(
        create_app(settings, secret_protector=UnavailableProtector())
    ) as client:
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
    with TestClient(
        create_app(settings, secret_protector=RecordingProtector())
    ) as client:
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
        assert client.get(
            f"/api/projects/{project['id']}/data-sources"
        ).json() == [response.json()]


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
            json={"name": "Warehouse", "dsn": "postgresql://localhost/review", secret_key: "x"},
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
            client.get(f"/api/data-sources/{hostile_id}/schema"),
        ):
            assert response.status_code == 404
            assert response.json() == {"detail": "Data source not found"}
            assert hostile_id not in response.text
