from __future__ import annotations

import hashlib
import json

from pydantic import field_validator
from pydantic_core import PydanticCustomError

from data_analysis_agent.data_sources import AccessLevel, DataSourceRepository
from data_analysis_agent.domain import TaskStatus
from data_analysis_agent.execution import QueryTimeoutError
from data_analysis_agent.permissions import ApprovalRepository, PermissionPolicy, ToolAction
from data_analysis_agent.postgres import SqlSafetyGateway
from data_analysis_agent.sql_safety import SqlAstValidator
from data_analysis_agent.team import AgentRole, TeamRepository
from data_analysis_agent.tools import (
    ToolCall,
    ToolContext,
    ToolDefinition,
    ToolHandlerError,
    ToolInput,
    ToolRegistry,
    build_tool_registry,
)
from data_analysis_agent.tracing import TraceRecorder, TraceRepository


def test_registry_exposes_exact_mvp_tool_set(tool_registry) -> None:
    assert {schema.name for schema in tool_registry.schemas()} == {
        "inspect_workspace",
        "inspect_dataset",
        "inspect_database_schema",
        "query_data",
        "query_database",
        "run_python",
        "save_artifact",
        "write_report",
        "update_plan",
        "request_user_input",
    }


def test_lead_coordination_tools_execute_with_real_registry_and_enforce_ownership(
    tool_context,
    db_connection,
    task_repository,
    project_repository,
    dataset_service,
    duckdb_executor,
    python_executor,
    workspace,
    artifact_repository,
    report_service,
) -> None:
    repository = TeamRepository(db_connection)
    tool_registry = build_tool_registry(
        datasets=dataset_service,
        duckdb_executor=duckdb_executor,
        python_executor=python_executor,
        workspace=workspace,
        artifacts=artifact_repository,
        reports=report_service,
        tasks=task_repository,
        permission_policy=PermissionPolicy(),
        approvals=ApprovalRepository(db_connection),
        include_coordination_tools=True,
    )

    class Coordinator:
        def spawn(self, task_id, role, objective):
            return repository.create_run(task_id, role, objective)

        def list(self, task_id):
            return repository.list_runs(task_id)

        def await_runs(self, run_ids, timeout_seconds):
            return tuple(repository.get_run(run_id) for run_id in run_ids)

        def cancel(self, run_id):
            return repository.request_cancel(run_id)

    tool_registry.configure_team(Coordinator(), repository)
    spawn = tool_registry.execute(
        ToolCall("spawn", "spawn_teammate", {"role": "sql", "objective": "Bounded query"}),
        tool_context,
    )
    assert spawn.success and spawn.output["agent_id"]
    run_id = spawn.output["agent_id"]
    listed = tool_registry.execute(ToolCall("list", "list_teammates", {}), tool_context)
    awaited = tool_registry.execute(
        ToolCall("await", "await_teammates", {"agent_ids": [run_id], "timeout_seconds": 1}),
        tool_context,
    )
    cancelled = tool_registry.execute(
        ToolCall("cancel", "cancel_teammate", {"agent_id": run_id}), tool_context
    )
    assert listed.success and listed.output["agents"][0]["agent_id"] == run_id
    assert awaited.success and awaited.output["agents"][0]["agent_id"] == run_id
    assert cancelled.success and cancelled.output["status"] == "created"

    other_project = project_repository.create("Other")
    other_task = task_repository.create(other_project.id, "Other task")
    foreign = repository.create_run(other_task.id, AgentRole.SQL, "Foreign")
    rejected = tool_registry.execute(
        ToolCall("foreign", "cancel_teammate", {"agent_id": foreign.id}), tool_context
    )
    assert rejected.error_category == "permission_denied"

    mixed = tool_registry.execute(
        ToolCall(
            "mixed",
            "await_teammates",
            {"agent_ids": [run_id, foreign.id], "timeout_seconds": 1},
        ),
        tool_context,
    )
    unknown_id = tool_registry.execute(
        ToolCall(
            "missing",
            "await_teammates",
            {"agent_ids": ["does-not-exist"], "timeout_seconds": 1},
        ),
        tool_context,
    )
    too_many = tool_registry.execute(
        ToolCall("many", "await_teammates", {"agent_ids": [run_id] * 4, "timeout_seconds": 1}),
        tool_context,
    )
    assert mixed.error_category == "permission_denied"
    assert unknown_id.error_category == "permission_denied"
    assert too_many.error_category == "validation_error"

    restricted = tool_registry.restrict(frozenset({"inspect_workspace"}))
    assert {schema.name for schema in restricted.schemas()} == {"inspect_workspace"}
    unknown = restricted.execute(ToolCall("no-op", "spawn_teammate", {}), tool_context)
    assert unknown.error_category == "unknown_tool"
    assert repository.get_run(foreign.id).cancel_requested is False


def test_await_teammates_pauses_lead_and_exposes_pending_approval(
    tool_context,
    db_connection,
    task_repository,
    dataset_service,
    duckdb_executor,
    python_executor,
    workspace,
    artifact_repository,
    report_service,
) -> None:
    approvals = ApprovalRepository(db_connection)
    repository = TeamRepository(db_connection)
    registry = build_tool_registry(
        datasets=dataset_service,
        duckdb_executor=duckdb_executor,
        python_executor=python_executor,
        workspace=workspace,
        artifacts=artifact_repository,
        reports=report_service,
        tasks=task_repository,
        permission_policy=PermissionPolicy(),
        approvals=approvals,
        include_coordination_tools=True,
    )
    run = repository.create_run(tool_context.task_id, AgentRole.SQL, "Governed query")
    repository.transition_run(run.id, "working")
    repository.transition_run(run.id, "waiting")
    task_repository.transition(tool_context.task_id, TaskStatus.NEEDS_INPUT)
    approval = approvals.request(
        tool_context.task_id,
        ToolAction("query_database", {"sql_hash": "hash", "policy_version": 3}),
        "Restricted fields require one-time approval",
    )

    class Coordinator:
        def await_runs(self, run_ids, timeout_seconds):
            del timeout_seconds
            return tuple(repository.get_run(run_id) for run_id in run_ids)

    registry.configure_team(Coordinator(), repository)
    result = registry.execute(
        ToolCall(
            "await",
            "await_teammates",
            {"agent_ids": [run.id], "timeout_seconds": 120},
        ),
        tool_context,
    )

    assert result.success is True
    assert result.pauses_task is True
    assert result.output["wait_reason"] == "approval_required"
    assert result.output["approvals"] == [
        {
            "approval_id": approval.id,
            "tool_name": "query_database",
            "reason": "Restricted fields require one-time approval",
        }
    ]


def test_registry_exposes_database_query_tool(tool_registry) -> None:
    names = {schema.name for schema in tool_registry.schemas()}
    assert {"inspect_database_schema", "query_database"} <= names


def test_inspect_database_schema_returns_visible_columns_without_query_approval(
    tool_context,
    source_repository,
    sql_gateway,
    dataset_service,
    duckdb_executor,
    python_executor,
    workspace,
    artifact_repository,
    report_service,
    task_repository,
    approval_repository,
) -> None:
    source = source_repository.create(
        tool_context.project_id,
        "Warehouse",
        "postgresql://analyst:secret@localhost/portfolio",
    )

    registry = build_tool_registry(
        datasets=dataset_service,
        duckdb_executor=duckdb_executor,
        python_executor=python_executor,
        workspace=workspace,
        artifacts=artifact_repository,
        reports=report_service,
        tasks=task_repository,
        permission_policy=PermissionPolicy(),
        approvals=approval_repository,
        sql_gateway=sql_gateway,
    )

    result = registry.execute(
        ToolCall(
            id="inspect-database",
            name="inspect_database_schema",
            arguments={"data_source_id": source.id},
        ),
        tool_context,
    )

    assert result.success is True
    assert result.output["data_source_id"] == source.id
    assert result.output["tables"] == [
        {"name": "orders", "columns": ["region", "amount", "customer_email"]}
    ]


def test_query_database_returns_structured_gateway_outcome(
    db_connection,
    settings,
    source_repository,
    approval_repository,
    artifact_repository,
    workspace,
    task_repository,
    fake_driver,
    tool_context,
    dataset_service,
    duckdb_executor,
    python_executor,
    report_service,
) -> None:
    source = source_repository.create(
        tool_context.project_id,
        "Warehouse",
        "postgresql://analyst:secret@localhost/portfolio",
    )
    source_repository.replace_policies(
        source.id,
        {("orders", None): AccessLevel.ALLOWED},
    )
    gateway = SqlSafetyGateway(
        connection=db_connection,
        settings=settings,
        sources=source_repository,
        validator=SqlAstValidator(),
        approvals=approval_repository,
        artifacts=artifact_repository,
        workspace=workspace,
        tasks=task_repository,
        driver=fake_driver,
    )
    database_tool_registry = build_tool_registry(
        datasets=dataset_service,
        duckdb_executor=duckdb_executor,
        python_executor=python_executor,
        workspace=workspace,
        artifacts=artifact_repository,
        reports=report_service,
        tasks=task_repository,
        permission_policy=PermissionPolicy(),
        approvals=approval_repository,
        sql_gateway=gateway,
        trace_recorder=TraceRecorder(TraceRepository(db_connection)),
    )

    analysis_run = TraceRepository(db_connection).start_run(tool_context.task_id)
    traced_context = ToolContext(
        project_id=tool_context.project_id,
        task_id=tool_context.task_id,
        run_id=analysis_run.id,
    )

    result = database_tool_registry.execute(
        ToolCall(
            id="database-call",
            name="query_database",
            arguments={"data_source_id": source.id, "sql": "SELECT region FROM orders"},
        ),
        traced_context,
    )

    assert result.success is True
    assert result.output["status"] == "succeeded"
    assert result.output["artifact_id"]
    assert "dsn" not in result.output
    assert "secret" not in str(result)
    audit = db_connection.execute(
        "SELECT arguments_json FROM tool_executions WHERE task_id = ?",
        (tool_context.task_id,),
    ).fetchone()[0]
    assert json.loads(audit) == {
        "data_source_id_hash": hashlib.sha256(source.id.encode("utf-8")).hexdigest(),
        "sql_hash": hashlib.sha256("SELECT region FROM orders".encode("utf-8")).hexdigest(),
    }
    sql_events = TraceRepository(db_connection).events_for_run(analysis_run.id)
    assert [event.event_type for event in sql_events] == [
        "sql.validated",
        "sql.explained",
        "sql.executed",
    ]
    assert sql_events[-1].artifact_ids == (result.output["artifact_id"],)


def test_inspect_dataset_tool_returns_real_profile(
    tool_registry,
    tool_context,
    dataset_artifact,
) -> None:
    result = tool_registry.execute(
        ToolCall(
            id="call-1",
            name="inspect_dataset",
            arguments={"artifact_id": dataset_artifact.id, "sample_rows": 2},
        ),
        tool_context,
    )

    assert result.success is True
    assert result.output["row_count"] == 12
    assert len(result.output["sample"]) == 2


def test_invalid_tool_arguments_return_structured_error(
    tool_registry,
    tool_context,
) -> None:
    result = tool_registry.execute(
        ToolCall(
            id="call-2",
            name="inspect_dataset",
            arguments={"sample_rows": "many"},
        ),
        tool_context,
    )

    assert result.success is False
    assert result.error_category == "validation_error"
    assert result.retryable is True


def test_tool_validation_never_reflects_sensitive_input(tool_registry, tool_context) -> None:
    secret = "postgresql://analyst:password@host/ciphertext"
    result = tool_registry.execute(
        ToolCall(
            id="validation-secret",
            name="query_database",
            arguments={
                "data_source_id": "source-1",
                "sql": secret * 2_000,
                "dsn": secret,
            },
        ),
        tool_context,
    )

    assert result.error_category == "validation_error"
    assert secret not in str(result)
    assert result.diagnostics is not None
    assert result.diagnostics == {"error_count": 2, "types": ["validation_error"]}


def test_tool_validation_never_reflects_sensitive_argument_name(
    tool_registry, tool_context
) -> None:
    secret_key = "postgresql://analyst:password@host/ciphertext"
    result = tool_registry.execute(
        ToolCall(
            id="validation-secret-key",
            name="query_database",
            arguments={
                "data_source_id": "source-1",
                "sql": "SELECT 1",
                secret_key: "x",
            },
        ),
        tool_context,
    )

    assert result.error_category == "validation_error"
    assert secret_key not in str(result)
    assert result.diagnostics == {"error_count": 1, "types": ["validation_error"]}


def test_tool_validation_never_reflects_custom_pydantic_error_type(
    tool_context,
    task_repository,
    artifact_repository,
    db_connection,
) -> None:
    class SecretTypeInput(ToolInput):
        value: str

        @field_validator("value")
        @classmethod
        def reject_value(cls, value: str) -> str:
            raise PydanticCustomError("SECRET", "secret")

    registry = ToolRegistry(
        (ToolDefinition("query_data", "test", SecretTypeInput, lambda *_: None),),
        PermissionPolicy(),
        ApprovalRepository(db_connection),
        task_repository,
        artifact_repository,
    )
    result = registry.execute(
        ToolCall(id="custom-validation", name="query_data", arguments={"value": "x"}),
        tool_context,
    )

    assert "SECRET" not in str(result)
    assert result.diagnostics == {"error_count": 1, "types": ["validation_error"]}


def test_tool_handler_error_never_reflects_or_persists_secret_diagnostics(
    tool_context,
    task_repository,
    artifact_repository,
    db_connection,
) -> None:
    secret = "postgresql://analyst:password@host/ciphertext"

    def fail(arguments: ToolInput, context) -> None:
        del arguments, context
        raise ToolHandlerError(secret, category="unsafe_category", diagnostics={"dsn": secret})

    registry = ToolRegistry(
        (ToolDefinition("query_data", "test", ToolInput, fail),),
        PermissionPolicy(),
        ApprovalRepository(db_connection),
        task_repository,
        artifact_repository,
    )
    result = registry.execute(
        ToolCall(id="unsafe-call", name="query_data", arguments={}), tool_context
    )

    assert result.error_category == "internal_tool_error"
    assert secret not in str(result)
    assert result.message == "Tool failed"
    assert result.diagnostics is None
    stored = db_connection.execute(
        "SELECT error_message FROM tool_executions WHERE task_id = ?",
        (tool_context.task_id,),
    ).fetchone()[0]
    assert stored == "Tool failed"
    assert (
        db_connection.execute(
            "SELECT error_category FROM tool_executions WHERE task_id = ?",
            (tool_context.task_id,),
        ).fetchone()[0]
        == "internal_tool_error"
    )
    assert (
        "unsafe_category"
        not in db_connection.execute(
            "SELECT payload_json FROM task_events WHERE task_id = ?",
            (tool_context.task_id,),
        ).fetchone()[0]
    )


def test_tool_rejects_cross_project_context_before_execution(
    tool_registry,
    tool_context,
    project_repository,
    task_repository,
    db_connection,
) -> None:
    other_project = project_repository.create("Other project")
    other_task = task_repository.create(other_project.id, "Other task")
    result = tool_registry.execute(
        ToolCall(id="cross-project", name="inspect_workspace", arguments={}),
        type(tool_context)(project_id=tool_context.project_id, task_id=other_task.id),
    )

    assert result.error_category == "project_scope_denied"
    assert result.retryable is False
    assert (
        db_connection.execute(
            "SELECT COUNT(*) FROM tool_executions WHERE task_id = ?", (other_task.id,)
        ).fetchone()[0]
        == 0
    )


def test_query_database_credential_failure_is_never_persisted_or_reflected(
    db_connection,
    settings,
    approval_repository,
    artifact_repository,
    workspace,
    task_repository,
    fake_driver,
    tool_context,
    dataset_service,
    duckdb_executor,
    python_executor,
    report_service,
) -> None:
    secret = "postgresql://analyst:password@host/ciphertext"

    class BrokenProtector:
        def protect(self, value: str) -> bytes:
            return value.encode()

        def unprotect(self, value: bytes) -> str:
            raise RuntimeError(secret)

    sources = DataSourceRepository(db_connection, BrokenProtector())
    source = sources.create(tool_context.project_id, "Warehouse", secret)
    gateway = SqlSafetyGateway(
        connection=db_connection,
        settings=settings,
        sources=sources,
        validator=SqlAstValidator(),
        approvals=approval_repository,
        artifacts=artifact_repository,
        workspace=workspace,
        tasks=task_repository,
        driver=fake_driver,
    )
    registry = build_tool_registry(
        datasets=dataset_service,
        duckdb_executor=duckdb_executor,
        python_executor=python_executor,
        workspace=workspace,
        artifacts=artifact_repository,
        reports=report_service,
        tasks=task_repository,
        permission_policy=PermissionPolicy(),
        approvals=approval_repository,
        sql_gateway=gateway,
    )

    result = registry.execute(
        ToolCall(
            id="credential-failure",
            name="query_database",
            arguments={"data_source_id": source.id, "sql": "SELECT region FROM orders"},
        ),
        tool_context,
    )

    assert result.error_category == "credential_error"
    assert secret not in str(result)
    stored = db_connection.execute(
        "SELECT error_message FROM tool_executions WHERE task_id = ?",
        (tool_context.task_id,),
    ).fetchone()[0]
    assert secret not in stored


def test_approved_tool_action_is_allowed_once(
    tool_registry,
    tool_context,
    task_repository,
    workspace,
    db_connection,
) -> None:
    path = workspace.resolve_inside(tool_context.project_id, "charts/review.html")
    path.write_text("<html>review</html>", encoding="utf-8")
    arguments = {
        "path": str(path),
        "artifact_type": "chart",
        "title": "Review chart",
    }
    call = ToolCall(id="approval-call", name="save_artifact", arguments=arguments)

    pending = tool_registry.execute(call, tool_context)
    assert pending.error_category == "approval_required"
    ApprovalRepository(db_connection).decide(pending.output["approval_id"], approved=True)
    task_repository.transition(tool_context.task_id, TaskStatus.UNDERSTANDING)

    allowed = tool_registry.execute(call, tool_context)

    assert allowed.success is True
    repeated = tool_registry.execute(call, tool_context)
    assert repeated.error_category == "approval_required"


def test_duckdb_timeout_is_returned_as_retryable_query_timeout(
    monkeypatch, tool_registry, duckdb_executor, task
) -> None:
    def timeout(*_args, **_kwargs):
        raise QueryTimeoutError("local query exceeded its time limit")

    monkeypatch.setattr(duckdb_executor, "query", timeout)

    result = tool_registry.execute(
        ToolCall("duck-timeout", "query_data", {"sql": "SELECT 1", "bindings": {}}),
        ToolContext(task.project_id, task.id),
    )

    assert result.success is False
    assert result.error_category == "query_timeout"
    assert result.retryable is True
    assert "time limit" not in result.message.lower()


def test_query_data_returns_binding_guidance_for_unknown_table(
    tool_registry, dataset_artifact, task
) -> None:
    result = tool_registry.execute(
        ToolCall(
            "duck-missing-table",
            "query_data",
            {
                "sql": "SELECT * FROM region_operations_2025",
                "bindings": {"dataset": dataset_artifact.id},
            },
        ),
        ToolContext(task.project_id, task.id),
    )

    assert result.success is False
    assert result.error_category == "unresolved_table"
    assert result.retryable is False
    assert result.diagnostics == {
        "available_tables": ["dataset"],
        "guidance": "Use a bindings key exactly as the SQL table name.",
    }
