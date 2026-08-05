from __future__ import annotations

from data_analysis_agent.domain import TaskStatus
from data_analysis_agent.permissions import ApprovalRepository
from data_analysis_agent.tools import ToolCall


def test_registry_exposes_exact_mvp_tool_set(tool_registry) -> None:
    assert {schema.name for schema in tool_registry.schemas()} == {
        "inspect_workspace",
        "inspect_dataset",
        "query_data",
        "run_python",
        "save_artifact",
        "write_report",
        "update_plan",
        "request_user_input",
    }


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
