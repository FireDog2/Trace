from __future__ import annotations

from dataclasses import replace

from data_analysis_agent.agent import AgentRunner, SYSTEM_PROMPT
from data_analysis_agent.domain import TaskStatus
from data_analysis_agent.providers import ModelResponse, ProviderError, ScriptedProvider
from data_analysis_agent.tracing import TraceRepository
from data_analysis_agent.tools import ToolCall
from data_analysis_agent.tools import ToolResult


def response_with_tool(name: str, arguments: dict) -> ModelResponse:
    return ModelResponse(
        text="",
        tool_calls=(ToolCall(id=f"call-{name}", name=name, arguments=arguments),),
    )


def response_with_text(text: str) -> ModelResponse:
    return ModelResponse(text=text, tool_calls=())


def test_lead_prompt_has_explicit_role_routing_contract() -> None:
    prompt = " ".join(SYSTEM_PROMPT.split())
    assert "Knowledge Agent" in prompt
    assert "SQL Agent" in prompt
    assert "Report Agent" in prompt
    assert "Lead must not" in prompt
    assert "at most three active teammates" in prompt


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

    class Protector:
        def protect(self, value): return b"cipher:" + value.encode()
        def unprotect(self, value): return value.removeprefix(b"cipher:").decode()
    provider = CapturingProvider()
    AgentRunner(settings=settings, provider=provider, tools=Registry(), tasks=task_repository, secret_protector=Protector()).run(task.id)

    persisted = "\n".join(message.content for message in task_repository.list_messages(task.id))
    assert sql not in persisted
    assert "source-secret" not in persisted
    assert "person@example.test" not in persisted
    next_request = provider.requests[1]
    assert any(sql in message.content for message in next_request.messages)
    assert any("person@example.test" in message.content for message in next_request.messages)


def test_database_tool_messages_are_encrypted_and_replayed_after_approval(
    settings, task_repository, task, db_connection
) -> None:
    sql = "SELECT customer_email FROM orders"

    class Protector:
        def protect(self, value: str) -> bytes:
            return b"cipher:" + value.encode()[::-1]

        def unprotect(self, value: bytes) -> str:
            return value.removeprefix(b"cipher:")[::-1].decode()

    class Registry:
        def schemas(self): return ()
        def execute(self, call, context):
            task_repository.transition(context.task_id, TaskStatus.NEEDS_INPUT)
            return ToolResult(call_id=call.id, success=False, output={}, error_category="approval_required", message="Approval required", pauses_task=True)

    class Provider:
        def __init__(self):
            self.requests = []
            self.responses = [response_with_tool("query_database", {"data_source_id": "source-1", "sql": sql}), response_with_text("done")]
        def complete(self, request):
            self.requests.append(request)
            return self.responses.pop(0)

    provider = Provider()
    runner = AgentRunner(settings=settings, provider=provider, tools=Registry(), tasks=task_repository, secret_protector=Protector())
    paused = runner.run(task.id)
    assert paused.status is TaskStatus.NEEDS_INPUT
    stored = "\n".join(message.content for message in task_repository.list_messages(task.id))
    assert sql not in stored and "source-1" not in stored
    ciphertext = db_connection.execute("SELECT protected_content FROM protected_tool_message_payloads").fetchone()[0]
    assert sql.encode() not in bytes(ciphertext)

    completed = runner.resume_approval(task.id)
    assert completed.status is TaskStatus.COMPLETED
    assert any(sql in message.content for message in provider.requests[-1].messages)


def test_pausing_tool_call_records_cancelled_results_for_remaining_calls(settings, task_repository, task) -> None:
    class Protector:
        def protect(self, value): return b"x" + value.encode()[::-1]
        def unprotect(self, value): return value[1:][::-1].decode()
    class Registry:
        def schemas(self): return ()
        def execute(self, call, context):
            task_repository.transition(context.task_id, TaskStatus.NEEDS_INPUT)
            return ToolResult(call_id=call.id, success=False, output={}, pauses_task=True)
    provider = ScriptedProvider([ModelResponse(text="", tool_calls=(
        ToolCall(id="pause", name="query_database", arguments={"data_source_id": "source", "sql": "SELECT x"}),
        ToolCall(id="later", name="inspect_workspace", arguments={}),
    ))])

    AgentRunner(settings=settings, provider=provider, tools=Registry(), tasks=task_repository, secret_protector=Protector()).run(task.id)

    tool_messages = [message.content for message in task_repository.list_messages(task.id) if message.role == "tool"]
    assert len(tool_messages) == 2
    assert "cancelled_due_to_pause" in tool_messages[1]
def test_agent_reports_bounded_recovery_metadata_to_model(
    settings, task_repository, task
) -> None:
    class Registry:
        def __init__(self) -> None:
            self.calls = 0

        def schemas(self):
            return ()

        def execute(self, call, context):
            del context
            self.calls += 1
            return ToolResult(
                call_id=call.id,
                success=False,
                output={},
                error_category="query_timeout",
                message="Database query could not be completed",
                retryable=True,
            )

    class Provider:
        def __init__(self) -> None:
            self.requests = []
            self.responses = [
                response_with_tool("inspect_workspace", {}),
                response_with_tool("inspect_workspace", {}),
                response_with_text("Used a safe fallback instead"),
            ]

        def complete(self, request):
            self.requests.append(request)
            return self.responses.pop(0)

    registry = Registry()
    provider = Provider()

    completed = AgentRunner(
        settings=settings,
        provider=provider,
        tools=registry,
        tasks=task_repository,
    ).run(task.id)

    assert completed.status is TaskStatus.COMPLETED
    assert registry.calls == 2
    first_retry = "\n".join(message.content for message in provider.requests[1].messages)
    exhausted = "\n".join(message.content for message in provider.requests[2].messages)
    assert '"attempt":1' in first_retry
    assert '"remaining":1' in first_retry
    assert '"action":"regenerate"' in first_retry
    assert '"attempt":2' in exhausted
    assert '"remaining":0' in exhausted
    assert '"action":"fallback"' in exhausted
    assert '"retryable":false' in exhausted


def test_agent_retries_transient_provider_error_with_bounded_backoff(
    settings, task_repository, tool_registry, task
) -> None:
    class Provider:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, request):
            del request
            self.calls += 1
            if self.calls < 3:
                raise ProviderError("rate_limit", "Please retry later")
            return response_with_text("Recovered")

    provider = Provider()
    delays: list[float] = []

    completed = AgentRunner(
        settings=replace(settings, model_max_retries=2),
        provider=provider,
        tools=tool_registry,
        tasks=task_repository,
        retry_sleep=delays.append,
    ).run(task.id)

    assert completed.status is TaskStatus.COMPLETED
    assert provider.calls == 3
    assert delays == [0.25, 0.5]


def test_agent_does_not_retry_non_transient_provider_error(
    settings, task_repository, tool_registry, task
) -> None:
    class Provider:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, request):
            del request
            self.calls += 1
            raise ProviderError("authentication", "Credentials rejected")

    provider = Provider()
    delays: list[float] = []

    failed = AgentRunner(
        settings=replace(settings, model_max_retries=2),
        provider=provider,
        tools=tool_registry,
        tasks=task_repository,
        retry_sleep=delays.append,
    ).run(task.id)

    assert failed.status is TaskStatus.FAILED
    assert provider.calls == 1
    assert delays == []


def test_failed_task_can_be_retried_with_a_new_user_message(
    settings, task_repository, tool_registry, task
) -> None:
    task_repository.transition(task.id, TaskStatus.FAILED)
    provider = ScriptedProvider(
        [ModelResponse(text="Recovered after retry.", tool_calls=())]
    )

    recovered = AgentRunner(
        settings=settings,
        provider=provider,
        tools=tool_registry,
        tasks=task_repository,
    ).retry(task.id, "Try the workspace inventory again.")

    assert recovered.status is TaskStatus.COMPLETED
    assert [message.content for message in task_repository.list_messages(task.id)] == [
        "Try the workspace inventory again.",
        "Recovered after retry.",
    ]
    assert any(
        event.event_type == "task.retried"
        for event in task_repository.events_after(task.id, 0)
    )


def test_agent_reactively_compacts_and_retries_context_rejection(
    settings, task_repository, tool_registry, task
) -> None:
    for index in range(6):
        task_repository.add_message(
            task.id,
            "user" if index % 2 == 0 else "assistant",
            f"historical turn {index}",
        )

    class Provider:
        def __init__(self) -> None:
            self.requests = []

        def complete(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                raise ProviderError(
                    "context_length_exceeded", "The model context exceeded the provider limit"
                )
            return response_with_text("Recovered after compact")

    provider = Provider()
    traces = TraceRepository(task_repository._connection)
    completed = AgentRunner(
        settings=replace(settings, context_recent_messages=2),
        provider=provider,
        tools=tool_registry,
        tasks=task_repository,
        traces=traces,
    ).run(task.id)

    assert completed.status is TaskStatus.COMPLETED
    assert len(provider.requests) == 2
    assert provider.requests[1].messages[0].role == "system"
    assert "Session checkpoint" in provider.requests[1].messages[0].content
    checkpoints = task_repository._connection.execute(
        "SELECT reason FROM context_checkpoints WHERE owner_type='task' AND owner_id=?",
        (task.id,),
    ).fetchall()
    assert [row[0] for row in checkpoints] == ["reactive"]
    events = traces.events_for_run(traces.list_runs(task.id)[0].id)
    assert "context.rejected" in [event.event_type for event in events]
    assert "context.compacted" in [event.event_type for event in events]
