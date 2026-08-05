from __future__ import annotations

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
