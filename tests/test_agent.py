from __future__ import annotations

from data_analysis_agent.agent import AgentRunner
from data_analysis_agent.domain import TaskStatus
from data_analysis_agent.providers import ModelResponse, ScriptedProvider
from data_analysis_agent.tools import ToolCall
from data_analysis_agent.tools import ToolResult


def response_with_tool(name: str, arguments: dict) -> ModelResponse:
    return ModelResponse(
        text="",
        tool_calls=(ToolCall(id=f"call-{name}", name=name, arguments=arguments),),
    )


def response_with_text(text: str) -> ModelResponse:
    return ModelResponse(text=text, tool_calls=())


def test_agent_executes_tools_and_completes(
    settings,
    task_repository,
    tool_registry,
    task,
) -> None:
    provider = ScriptedProvider(
        [
            response_with_tool(
                "update_plan",
                {"steps": ["Inspect data"], "reason": "Initial plan"},
            ),
            response_with_tool("inspect_workspace", {}),
            response_with_text("Analysis completed"),
        ]
    )

    completed = AgentRunner(
        settings=settings,
        provider=provider,
        tools=tool_registry,
        tasks=task_repository,
    ).run(task.id)

    assert completed.status is TaskStatus.COMPLETED
    assert [message.role for message in task_repository.list_messages(task.id)] == [
        "assistant_tool_call",
        "tool",
        "assistant_tool_call",
        "tool",
        "assistant",
    ]


def test_agent_pauses_for_business_input(
    settings,
    task_repository,
    tool_registry,
    task,
) -> None:
    provider = ScriptedProvider(
        [
            response_with_tool(
                "request_user_input",
                {"question": "Which profit definition should be used?"},
            )
        ]
    )

    paused = AgentRunner(
        settings=settings,
        provider=provider,
        tools=tool_registry,
        tasks=task_repository,
    ).run(task.id)

    assert paused.status is TaskStatus.NEEDS_INPUT
    assert "profit definition" in task_repository.list_messages(task.id)[0].content


def test_agent_resumes_after_approval_without_fake_user_message(
    settings,
    task_repository,
    tool_registry,
    task,
) -> None:
    task_repository.transition(task.id, TaskStatus.NEEDS_INPUT)
    provider = ScriptedProvider([response_with_text("Approved action completed")])

    completed = AgentRunner(
        settings=settings,
        provider=provider,
        tools=tool_registry,
        tasks=task_repository,
    ).resume_approval(task.id)

    assert completed.status is TaskStatus.COMPLETED
    assert [message.role for message in task_repository.list_messages(task.id)] == [
        "assistant"
    ]


def test_database_rich_result_is_current_run_only_and_persisted_as_safe_summary(
    settings, task_repository, task
) -> None:
    sql = "SELECT customer_email FROM orders"
    preview = [{"customer_email": "person@example.test"}]

    class Registry:
        def schemas(self):
            return ()

        def execute(self, call, context):
            del call, context
            return ToolResult(
                call_id="db-call", success=True,
                output={"status": "succeeded", "query_hash": "hash", "row_count": 1,
                        "risk": "low", "artifact_id": "artifact-1", "preview": preview},
            )

    class CapturingProvider:
        def __init__(self):
            self.requests = []
            self.responses = [response_with_tool("query_database", {"data_source_id": "source-secret", "sql": sql}), response_with_text("done")]

        def complete(self, request):
            self.requests.append(request)
            return self.responses.pop(0)

    provider = CapturingProvider()
    AgentRunner(settings=settings, provider=provider, tools=Registry(), tasks=task_repository).run(task.id)

    persisted = "\n".join(message.content for message in task_repository.list_messages(task.id))
    assert sql not in persisted
    assert "source-secret" not in persisted
    assert "person@example.test" not in persisted
    next_request = provider.requests[1]
    assert any(sql in message.content for message in next_request.messages)
    assert any("person@example.test" in message.content for message in next_request.messages)
