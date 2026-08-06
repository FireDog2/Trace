from __future__ import annotations

from data_analysis_agent.domain import TaskStatus
from data_analysis_agent.permissions import ApprovalRepository, PermissionPolicy
from data_analysis_agent.data_sources import AccessLevel, DataSourceRepository
from data_analysis_agent.postgres import SqlSafetyGateway
from data_analysis_agent.sql_safety import SqlAstValidator
from data_analysis_agent.tools import ToolCall, build_tool_registry


def test_registry_exposes_exact_mvp_tool_set(tool_registry) -> None:
    assert {schema.name for schema in tool_registry.schemas()} == {
        "inspect_workspace",
        "inspect_dataset",
        "query_data",
        "query_database",
        "run_python",
        "save_artifact",
        "write_report",
        "update_plan",
        "request_user_input",
    }


def test_registry_exposes_database_query_tool(tool_registry) -> None:
    assert "query_database" in {schema.name for schema in tool_registry.schemas()}


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
    )

    result = database_tool_registry.execute(
        ToolCall(
            id="database-call",
            name="query_database",
            arguments={"data_source_id": source.id, "sql": "SELECT region FROM orders"},
        ),
        tool_context,
    )

    assert result.success is True
    assert result.output["status"] == "succeeded"
    assert result.output["artifact_id"]
    assert "dsn" not in result.output
    assert "secret" not in str(result)


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
            arguments={"data_source_id": "source-1", "sql": secret * 2_000, "dsn": secret},
        ),
        tool_context,
    )

    assert result.error_category == "validation_error"
    assert secret not in str(result)
    assert result.diagnostics is not None
    assert result.diagnostics["errors"][0].keys() <= {"loc", "type", "msg"}


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
    assert db_connection.execute(
        "SELECT COUNT(*) FROM tool_executions WHERE task_id = ?", (other_task.id,)
    ).fetchone()[0] == 0


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
