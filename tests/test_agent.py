from __future__ import annotations

from data_analysis_agent.agent import AgentRunner
from data_analysis_agent.domain import TaskStatus
from data_analysis_agent.providers import ModelResponse, ScriptedProvider
from data_analysis_agent.tools import ToolCall


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
        "tool",
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
