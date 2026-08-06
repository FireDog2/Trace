from __future__ import annotations

import json
from dataclasses import replace
from threading import Event

import pytest

from data_analysis_agent.model_config import SecretProtector
from data_analysis_agent.providers import ModelResponse, ProviderError, ScriptedProvider
from data_analysis_agent.team import AgentRole, AgentRunStatus, TeamRepository, TeammateRunner
from data_analysis_agent.tools import ToolCall, ToolContext, ToolResult


class Protector:
    def protect(self, value: str) -> bytes:
        return b"cipher:" + value.encode()[::-1]

    def unprotect(self, value: bytes) -> str:
        return value.removeprefix(b"cipher:")[::-1].decode()


def test_restricted_registry_exposes_only_sql_role_tools(tool_registry, tool_context) -> None:
    """Removing a tool from a role registry prevents all execution side effects."""
    restricted = tool_registry.restrict({"inspect_workspace", "inspect_dataset", "query_data", "query_database"})

    assert {item.name for item in restricted.schemas()} == {
        "inspect_workspace", "inspect_dataset", "query_data", "query_database"
    }
    result = restricted.execute(
        ToolCall(id="forbidden", name="spawn_teammate", arguments={}),
        ToolContext(project_id=tool_context.project_id, task_id=tool_context.task_id, agent_id="a1", run_id="r1"),
    )
    assert result.error_category == "unknown_tool"


def test_teammate_keeps_history_out_of_parent_conversation(settings, db_connection, task_repository, task, tool_registry) -> None:
    """A teammate handoff is durable without leaking into the lead transcript."""
    repository = TeamRepository(db_connection)
    run = repository.create_run(task.id, AgentRole.KNOWLEDGE, "Find profit definition")

    completed = TeammateRunner(
        settings=settings, provider=ScriptedProvider([ModelResponse('{"summary":"Use gross profit"}', ())]),
        tools=tool_registry, repository=repository,
    ).run(run.id)

    assert completed.status is AgentRunStatus.COMPLETED
    assert json.loads(completed.result_json or "") == {"summary": "Use gross profit"}
    assert task_repository.list_messages(task.id) == ()


def test_teammate_fails_after_turn_budget(settings, db_connection, task, tool_registry) -> None:
    """A model that only emits calls cannot consume unlimited turns."""
    repository = TeamRepository(db_connection)
    run = repository.create_run(task.id, AgentRole.SQL, "Loop forever")
    limited = replace(settings, max_teammate_turns=1)
    provider = ScriptedProvider([ModelResponse("", (ToolCall("loop", "inspect_workspace", {}),))])

    failed = TeammateRunner(settings=limited, provider=provider, tools=tool_registry, repository=repository).run(run.id)

    assert failed.status is AgentRunStatus.FAILED
    assert failed.error_category == "agent_turn_limit"


def test_teammate_requires_json_object_handoff(settings, db_connection, task, tool_registry) -> None:
    """Plain text and JSON arrays are not structured teammate handoffs."""
    repository = TeamRepository(db_connection)
    run = repository.create_run(task.id, AgentRole.KNOWLEDGE, "Find definition")

    failed = TeammateRunner(
        settings=settings, provider=ScriptedProvider([ModelResponse("[]", ())]), tools=tool_registry, repository=repository,
    ).run(run.id)

    assert failed.status is AgentRunStatus.FAILED
    assert failed.error_category == "invalid_handoff"


def test_provider_error_has_safe_category(settings, db_connection, task, tool_registry) -> None:
    """Provider implementation details never become teammate errors."""
    class BrokenProvider:
        def complete(self, request):
            del request
            raise ProviderError("network", "api key is secret")

    repository = TeamRepository(db_connection)
    run = repository.create_run(task.id, AgentRole.KNOWLEDGE, "Find definition")
    failed = TeammateRunner(settings=settings, provider=BrokenProvider(), tools=tool_registry, repository=repository).run(run.id)

    assert failed.error_category == "provider_error"
    assert "secret" not in (failed.error_message or "")


def test_sql_teammate_sql_and_preview_are_encrypted_then_replayed(settings, db_connection, task, tool_registry) -> None:
    """SQL teammate history stores only safe summaries but provides exact context to the next call."""
    sql = "SELECT customer_email FROM orders"
    preview = [{"customer_email": "person@example.test"}]

    class Registry:
        def restrict(self, allowed):
            assert "query_database" in allowed
            return self
        def schemas(self): return ()
        def execute(self, call, context):
            assert context.agent_id and context.run_id
            return ToolResult(call.id, True, {"status": "succeeded", "preview": preview, "artifact_id": "a1"})

    repository = TeamRepository(db_connection)
    run = repository.create_run(task.id, AgentRole.SQL, "Query customers")
    provider = ScriptedProvider([
        ModelResponse("", (ToolCall("db", "query_database", {"data_source_id": "source-secret", "sql": sql}),)),
        ModelResponse('{"artifact_id":"a1"}', ()),
    ])

    completed = TeammateRunner(settings=settings, provider=provider, tools=Registry(), repository=repository, secret_protector=Protector()).run(run.id)

    assert completed.status is AgentRunStatus.COMPLETED
    stored = "\n".join(message.content for message in repository.list_messages(run.id))
    assert sql not in stored and "source-secret" not in stored and "person@example.test" not in stored
    ciphertext = db_connection.execute("SELECT protected_content FROM protected_agent_message_payloads").fetchone()[0]
    assert sql.encode() not in bytes(ciphertext)
    replayed = "\n".join(message.content for message in provider.requests[-1].messages)
    assert sql in replayed and "person@example.test" in replayed


def test_teammate_cancellation_before_each_call_and_remaining_call_results(settings, db_connection, task, tool_registry) -> None:
    """Cancellation prevents a following model call and pairs skipped calls with tool results."""
    repository = TeamRepository(db_connection)
    run = repository.create_run(task.id, AgentRole.KNOWLEDGE, "Inspect workspace")
    cancelled = Event()

    class Registry:
        def restrict(self, allowed): return self
        def schemas(self): return ()
        def execute(self, call, context):
            del call, context
            cancelled.set()
            return ToolResult("one", True, {})

    provider = ScriptedProvider([ModelResponse("", (ToolCall("one", "inspect_workspace", {}), ToolCall("two", "inspect_dataset", {"artifact_id": "x"})))])
    result = TeammateRunner(settings=settings, provider=provider, tools=Registry(), repository=repository, cancel_event=cancelled).run(run.id)

    assert result.status is AgentRunStatus.CANCELLED
    messages = repository.list_messages(run.id)
    assert [message.role for message in messages] == ["assistant_tool_call", "tool", "tool"]
    assert "cancelled_due_to_cancel" in messages[-1].content
