from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from queue import Queue
from threading import Barrier, Event, Lock, Thread

import pytest

import data_analysis_agent.team as team_module
from data_analysis_agent.db import connect_database
from data_analysis_agent.providers import ModelResponse, ProviderError, ScriptedProvider
from data_analysis_agent.repositories import ProjectRepository, TaskRepository
from data_analysis_agent.team import (
    AgentRole,
    AgentRun,
    AgentRunStatus,
    TeammateRunner,
    TeamRepository,
)
from data_analysis_agent.tools import ToolCall, ToolContext, ToolResult


class Protector:
    def protect(self, value: str) -> bytes:
        return b"cipher:" + value.encode()[::-1]

    def unprotect(self, value: bytes) -> str:
        return value.removeprefix(b"cipher:")[::-1].decode()


def test_restricted_registry_exposes_only_sql_role_tools(tool_registry, tool_context) -> None:
    """Removing a tool from a role registry prevents all execution side effects."""
    restricted = tool_registry.restrict(
        {
            "inspect_workspace",
            "inspect_dataset",
            "inspect_database_schema",
            "query_data",
            "query_database",
        }
    )

    assert {item.name for item in restricted.schemas()} == {
        "inspect_workspace",
        "inspect_dataset",
        "inspect_database_schema",
        "query_data",
        "query_database",
    }
    result = restricted.execute(
        ToolCall(id="forbidden", name="spawn_teammate", arguments={}),
        ToolContext(
            project_id=tool_context.project_id,
            task_id=tool_context.task_id,
            agent_id="a1",
            run_id="r1",
        ),
    )
    assert result.error_category == "unknown_tool"


def test_teammate_keeps_history_out_of_parent_conversation(
    settings, db_connection, task_repository, task, tool_registry
) -> None:
    """A teammate handoff is durable without leaking into the lead transcript."""
    repository = TeamRepository(db_connection)
    run = repository.create_run(task.id, AgentRole.KNOWLEDGE, "Find profit definition")

    completed = TeammateRunner(
        settings=settings,
        provider=ScriptedProvider([ModelResponse('{"summary":"Use gross profit"}', ())]),
        tools=tool_registry,
        repository=repository,
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

    failed = TeammateRunner(
        settings=limited, provider=provider, tools=tool_registry, repository=repository
    ).run(run.id)

    assert failed.status is AgentRunStatus.FAILED
    assert failed.error_category == "agent_turn_limit"


def test_teammate_requires_json_object_handoff(
    settings, db_connection, task, tool_registry
) -> None:
    """A second invalid response exhausts the bounded handoff repair."""
    repository = TeamRepository(db_connection)
    run = repository.create_run(task.id, AgentRole.KNOWLEDGE, "Find definition")

    failed = TeammateRunner(
        settings=settings,
        provider=ScriptedProvider([ModelResponse("[]", ()), ModelResponse("still invalid", ())]),
        tools=tool_registry,
        repository=repository,
    ).run(run.id)

    assert failed.status is AgentRunStatus.FAILED
    assert failed.error_category == "invalid_handoff"


def test_teammate_repairs_one_invalid_handoff(
    settings, db_connection, task, tool_registry
) -> None:
    repository = TeamRepository(db_connection)
    run = repository.create_run(task.id, AgentRole.SQL, "Return query evidence")
    provider = ScriptedProvider(
        [
            ModelResponse("Query completed successfully.", ()),
            ModelResponse('{"summary":"Query completed","artifact_ids":["a1"]}', ()),
        ]
    )

    completed = TeammateRunner(
        settings=settings,
        provider=provider,
        tools=tool_registry,
        repository=repository,
    ).run(run.id)

    assert completed.status is AgentRunStatus.COMPLETED
    assert json.loads(completed.result_json or "") == {
        "summary": "Query completed",
        "artifact_ids": ["a1"],
    }
    assert [message.role for message in repository.list_messages(run.id)] == [
        "system",
        "assistant",
    ]


def test_provider_error_has_safe_category(settings, db_connection, task, tool_registry) -> None:
    """Provider implementation details never become teammate errors."""

    class BrokenProvider:
        def complete(self, request):
            del request
            raise ProviderError("authentication", "api key is secret")

    repository = TeamRepository(db_connection)
    run = repository.create_run(task.id, AgentRole.KNOWLEDGE, "Find definition")
    failed = TeammateRunner(
        settings=settings,
        provider=BrokenProvider(),
        tools=tool_registry,
        repository=repository,
    ).run(run.id)

    assert failed.error_category == "provider_error"
    assert "secret" not in (failed.error_message or "")


def test_teammate_retries_transient_provider_errors_with_bounded_backoff(
    settings, db_connection, task, tool_registry
) -> None:
    class RecoveringProvider:
        def __init__(self):
            self.calls = 0

        def complete(self, request):
            del request
            self.calls += 1
            if self.calls < 3:
                raise ProviderError("network", "temporary")
            return ModelResponse('{"recovered":true}', ())

    repository = TeamRepository(db_connection)
    run = repository.create_run(task.id, AgentRole.KNOWLEDGE, "Recover provider")
    provider = RecoveringProvider()
    delays: list[float] = []

    completed = TeammateRunner(
        settings=replace(settings, model_max_retries=2),
        provider=provider,
        tools=tool_registry,
        repository=repository,
        retry_sleep=delays.append,
    ).run(run.id)

    assert completed.status is AgentRunStatus.COMPLETED
    assert provider.calls == 3
    assert delays == [0.25, 0.5]


def test_sql_teammate_reports_retry_budget_then_fallback_to_model(
    settings, db_connection, task
) -> None:
    class TimeoutRegistry:
        def __init__(self):
            self.calls = 0

        def restrict(self, allowed):
            assert "query_database" in allowed
            return self

        def schemas(self):
            return ()

        def execute(self, call, context):
            del context
            self.calls += 1
            return ToolResult(
                call.id,
                False,
                {},
                "query_timeout",
                "Database query could not be completed",
                retryable=True,
            )

    repository = TeamRepository(db_connection)
    run = repository.create_run(task.id, AgentRole.SQL, "Query with recovery")

    def call(call_id: str) -> ToolCall:
        return ToolCall(
            call_id,
            "query_database",
            {"data_source_id": "source", "sql": "SELECT 1"},
        )

    provider = ScriptedProvider(
        [
            ModelResponse("", (call("first"),)),
            ModelResponse("", (call("second"),)),
            ModelResponse("", (call("blocked"),)),
            ModelResponse('{"status":"fallback"}', ()),
        ]
    )
    registry = TimeoutRegistry()

    completed = TeammateRunner(
        settings=replace(settings, max_tool_attempts=2),
        provider=provider,
        tools=registry,
        repository=repository,
        secret_protector=Protector(),
    ).run(run.id)

    assert completed.status is AgentRunStatus.COMPLETED
    assert registry.calls == 2
    first_result = json.loads(provider.requests[1].messages[-1].content)
    second_result = json.loads(provider.requests[2].messages[-1].content)
    blocked_result = json.loads(provider.requests[3].messages[-1].content)
    assert first_result["retryable"] is True
    assert first_result["diagnostics"]["recovery"] == {
        "attempt": 1,
        "remaining": 1,
        "retry_allowed": True,
        "action": "regenerate",
        "fallback_category": None,
    }
    assert second_result["retryable"] is False
    assert second_result["diagnostics"]["recovery"]["action"] == "fallback"
    assert second_result["diagnostics"]["recovery"]["fallback_category"] == "tool_retry_exhausted"
    assert blocked_result["error_category"] == "tool_retry_exhausted"
    assert blocked_result["diagnostics"]["recovery"]["action"] == "fallback"


def test_sql_teammate_sql_and_preview_are_encrypted_then_replayed(
    settings, db_connection, task, tool_registry
) -> None:
    """SQL teammate history stores only safe summaries but provides exact context to the next call."""
    sql = "SELECT customer_email FROM orders"
    preview = [{"customer_email": "person@example.test"}]

    class Registry:
        def restrict(self, allowed):
            assert "query_database" in allowed
            return self

        def schemas(self):
            return ()

        def execute(self, call, context):
            assert context.agent_id and context.run_id
            return ToolResult(
                call.id,
                True,
                {"status": "succeeded", "preview": preview, "artifact_id": "a1"},
            )

    repository = TeamRepository(db_connection)
    run = repository.create_run(task.id, AgentRole.SQL, "Query customers")
    provider = ScriptedProvider(
        [
            ModelResponse(
                "",
                (
                    ToolCall(
                        "db",
                        "query_database",
                        {"data_source_id": "source-secret", "sql": sql},
                    ),
                ),
            ),
            ModelResponse('{"artifact_id":"a1"}', ()),
        ]
    )

    completed = TeammateRunner(
        settings=settings,
        provider=provider,
        tools=Registry(),
        repository=repository,
        secret_protector=Protector(),
    ).run(run.id)

    assert completed.status is AgentRunStatus.COMPLETED
    stored = "\n".join(message.content for message in repository.list_messages(run.id))
    assert (
        sql not in stored and "source-secret" not in stored and "person@example.test" not in stored
    )
    ciphertext = db_connection.execute(
        "SELECT protected_content FROM protected_agent_message_payloads"
    ).fetchone()[0]
    assert sql.encode() not in bytes(ciphertext)
    replayed = "\n".join(message.content for message in provider.requests[-1].messages)
    assert sql in replayed and "person@example.test" in replayed


def test_sql_teammate_replays_exact_approved_call_before_asking_model(
    settings, db_connection, task
) -> None:
    """Approval resumes the bound SQL call instead of asking the model to regenerate it."""
    sql = "SELECT logistics_cost FROM region_operations"

    class Registry:
        def __init__(self) -> None:
            self.calls: list[ToolCall] = []

        def restrict(self, allowed):
            assert "query_database" in allowed
            return self

        def schemas(self):
            return ()

        def execute(self, call, context):
            del context
            self.calls.append(call)
            if len(self.calls) == 1:
                return ToolResult(
                    call.id,
                    False,
                    {},
                    "approval_required",
                    "Approval required",
                    pauses_task=True,
                )
            return ToolResult(
                call.id,
                True,
                {"status": "succeeded", "artifact_id": "approved-result"},
            )

    repository = TeamRepository(db_connection)
    run = repository.create_run(task.id, AgentRole.SQL, "Read the restricted cost field")
    provider = ScriptedProvider(
        [
            ModelResponse(
                "",
                (
                    ToolCall(
                        "restricted-query",
                        "query_database",
                        {"data_source_id": "operations", "sql": sql},
                    ),
                ),
            ),
            ModelResponse('{"artifact_id":"approved-result"}', ()),
        ]
    )
    registry = Registry()
    runner = TeammateRunner(
        settings=settings,
        provider=provider,
        tools=registry,
        repository=repository,
        secret_protector=Protector(),
    )

    paused = runner.run(run.id)
    completed = runner.run(run.id)

    assert paused.status is AgentRunStatus.WAITING
    assert completed.status is AgentRunStatus.COMPLETED
    assert len(registry.calls) == 2
    assert registry.calls[1].arguments == registry.calls[0].arguments
    assert registry.calls[1].id != registry.calls[0].id
    assert len(provider.requests) == 2


def test_sql_teammate_blocks_an_exact_query_after_it_already_succeeded(
    settings, db_connection, task
) -> None:
    sql = "SELECT month, region, logistics_cost FROM region_operations ORDER BY month, region"

    class Registry:
        def __init__(self) -> None:
            self.calls = 0

        def restrict(self, allowed):
            assert "query_database" in allowed
            return self

        def schemas(self):
            return ()

        def execute(self, call, context):
            del context
            self.calls += 1
            return ToolResult(
                call.id,
                True,
                {"status": "succeeded", "artifact_id": "existing-result"},
            )

    def query(call_id: str) -> ToolCall:
        return ToolCall(
            call_id,
            "query_database",
            {"data_source_id": "operations", "sql": sql},
        )

    repository = TeamRepository(db_connection)
    run = repository.create_run(task.id, AgentRole.SQL, "Read logistics cost")
    provider = ScriptedProvider(
        [
            ModelResponse("", (query("first"),)),
            ModelResponse("", (query("duplicate"),)),
            ModelResponse('{"artifact_id":"existing-result"}', ()),
        ]
    )
    registry = Registry()

    completed = TeammateRunner(
        settings=settings,
        provider=provider,
        tools=registry,
        repository=repository,
        secret_protector=Protector(),
    ).run(run.id)

    assert completed.status is AgentRunStatus.COMPLETED
    assert registry.calls == 1
    duplicate_result = json.loads(provider.requests[-1].messages[-1].content)
    assert duplicate_result["error_category"] == "duplicate_successful_call"


def test_teammate_cancellation_before_each_call_and_remaining_call_results(
    settings, db_connection, task, tool_registry
) -> None:
    """Cancellation prevents a following model call and pairs skipped calls with tool results."""
    repository = TeamRepository(db_connection)
    run = repository.create_run(task.id, AgentRole.KNOWLEDGE, "Inspect workspace")
    cancelled = Event()

    class Registry:
        def restrict(self, allowed):
            return self

        def schemas(self):
            return ()

        def execute(self, call, context):
            del call, context
            cancelled.set()
            return ToolResult("one", True, {})

    provider = ScriptedProvider(
        [
            ModelResponse(
                "",
                (
                    ToolCall("one", "inspect_workspace", {}),
                    ToolCall("two", "inspect_dataset", {"artifact_id": "x"}),
                ),
            )
        ]
    )
    result = TeammateRunner(
        settings=settings,
        provider=provider,
        tools=Registry(),
        repository=repository,
        cancel_event=cancelled,
    ).run(run.id)

    assert result.status is AgentRunStatus.CANCELLED
    messages = repository.list_messages(run.id)
    assert [message.role for message in messages] == [
        "assistant_tool_call",
        "tool",
        "tool",
    ]
    assert "cancelled_due_to_cancel" in messages[-1].content


def test_response_boundary_cancellation_wins_before_handoff_or_tool_history(
    settings, db_connection, task, tool_registry
) -> None:
    """A cancellation raised during a blocking model call wins before its response is persisted."""
    repository = TeamRepository(db_connection)
    run = repository.create_run(task.id, AgentRole.KNOWLEDGE, "Inspect workspace")
    cancelled = Event()

    class Provider:
        def complete(self, request):
            del request
            cancelled.set()
            return ModelResponse('{"would":"complete"}', ())

    result = TeammateRunner(
        settings=settings,
        provider=Provider(),
        tools=tool_registry,
        repository=repository,
        cancel_event=cancelled,
    ).run(run.id)

    assert result.status is AgentRunStatus.CANCELLED
    assert repository.list_messages(run.id) == ()


def test_response_boundary_deadline_wins_before_handoff(
    settings, db_connection, task, tool_registry, monkeypatch
) -> None:
    """A model response arriving after deadline cannot complete the teammate."""
    repository = TeamRepository(db_connection)
    run = repository.create_run(task.id, AgentRole.KNOWLEDGE, "Inspect workspace")
    now = ["2026-08-06T00:00:00+00:00"]
    monkeypatch.setattr(team_module, "utc_now", lambda: now[0])

    class LateProvider:
        def complete(self, request):
            del request
            now[0] = "2026-08-06T00:00:02+00:00"
            return ModelResponse('{"would":"complete"}', ())

    result = TeammateRunner(
        settings=replace(settings, teammate_timeout_seconds=1),
        provider=LateProvider(),
        tools=tool_registry,
        repository=repository,
    ).run(run.id)

    assert result.status is AgentRunStatus.FAILED
    assert result.error_category == "agent_timeout"
    assert repository.list_messages(run.id) == ()


def test_safe_database_error_result_does_not_require_second_protection(
    settings, db_connection, task
) -> None:
    """A failed database tool has an already-safe result message and must not encrypt it again."""

    class ProtectOnlyOnce:
        def __init__(self):
            self.calls = 0

        def protect(self, value):
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("protector unavailable")
            return b"cipher:" + value.encode()

        def unprotect(self, value):
            return value.removeprefix(b"cipher:").decode()

    class Registry:
        def restrict(self, allowed):
            return self

        def schemas(self):
            return ()

        def execute(self, call, context):
            del context
            return ToolResult(call.id, False, {}, "internal_tool_error", "Tool failed")

    repository = TeamRepository(db_connection)
    run = repository.create_run(task.id, AgentRole.SQL, "Query customers")
    protector = ProtectOnlyOnce()
    result = TeammateRunner(
        settings=settings,
        provider=ScriptedProvider(
            [
                ModelResponse(
                    "",
                    (
                        ToolCall(
                            "db",
                            "query_database",
                            {"data_source_id": "source", "sql": "SELECT 1"},
                        ),
                    ),
                ),
                ModelResponse('{"status":"handled"}', ()),
            ]
        ),
        tools=Registry(),
        repository=repository,
        secret_protector=protector,
    ).run(run.id)

    assert result.status is AgentRunStatus.COMPLETED
    assert protector.calls == 1
    assert (
        db_connection.execute("SELECT COUNT(*) FROM protected_agent_message_payloads").fetchone()[0]
        == 1
    )


def test_throwing_database_tool_with_failing_later_protection_terminates_and_pairs_calls(
    settings, db_connection, task
) -> None:
    """A tool exception cannot strand a run, and skipped sibling calls still receive results."""

    class ProtectOnlyOnce:
        def __init__(self):
            self.calls = 0

        def protect(self, value):
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("protector unavailable")
            return b"cipher:" + value.encode()

        def unprotect(self, value):
            return value.removeprefix(b"cipher:").decode()

    class Registry:
        def restrict(self, allowed):
            return self

        def schemas(self):
            return ()

        def execute(self, call, context):
            del call, context
            raise RuntimeError("tool secret")

    repository = TeamRepository(db_connection)
    run = repository.create_run(task.id, AgentRole.SQL, "Query customers")
    result = TeammateRunner(
        settings=settings,
        provider=ScriptedProvider(
            [
                ModelResponse(
                    "",
                    (
                        ToolCall(
                            "db",
                            "query_database",
                            {"data_source_id": "source", "sql": "SELECT 1"},
                        ),
                        ToolCall("later", "inspect_workspace", {}),
                    ),
                )
            ]
        ),
        tools=Registry(),
        repository=repository,
        secret_protector=ProtectOnlyOnce(),
    ).run(run.id)

    assert result.status is AgentRunStatus.FAILED
    assert result.error_category == "tool_error"
    messages = repository.list_messages(run.id)
    assert [message.role for message in messages] == [
        "assistant_tool_call",
        "tool",
        "tool",
    ]
    assert "cancelled_due_to_tool_error" in messages[-1].content


def test_tool_response_boundary_discards_secret_result_and_pairs_timeout(
    settings, db_connection, task
) -> None:
    """A tool finishing after deadline cannot persist its sensitive output or call siblings."""
    secret = "customer@example.test"
    now = ["2026-08-06T00:00:00+00:00"]

    class Registry:
        def restrict(self, allowed):
            return self

        def schemas(self):
            return ()

        def execute(self, call, context):
            del call, context
            now[0] = "2026-08-06T00:00:02+00:00"
            return ToolResult("first", True, {"preview": [{"email": secret}]})

    repository = TeamRepository(db_connection)
    run = repository.create_run(task.id, AgentRole.KNOWLEDGE, "Inspect workspace")
    original_now = team_module.utc_now
    team_module.utc_now = lambda: now[0]
    try:
        result = TeammateRunner(
            settings=replace(settings, teammate_timeout_seconds=1),
            provider=ScriptedProvider(
                [
                    ModelResponse(
                        "",
                        (
                            ToolCall("first", "inspect_workspace", {}),
                            ToolCall("later", "inspect_dataset", {"artifact_id": "x"}),
                        ),
                    )
                ]
            ),
            tools=Registry(),
            repository=repository,
        ).run(run.id)
    finally:
        team_module.utc_now = original_now

    assert result.status is AgentRunStatus.FAILED
    assert result.error_category == "agent_timeout"
    stored = "\n".join(message.content for message in repository.list_messages(run.id))
    assert secret not in stored
    assert [message.role for message in repository.list_messages(run.id)] == [
        "assistant_tool_call",
        "tool",
        "tool",
    ]
    assert "cancelled_due_to_timeout" in stored


def test_waiting_run_reserves_turns_across_resumes_without_extending_deadline(
    settings, db_connection, task
) -> None:
    """A durable run's budget and absolute deadline survive a WAITING resume."""
    repository = TeamRepository(db_connection)
    run = repository.create_run(task.id, AgentRole.KNOWLEDGE, "Inspect workspace")

    class PausingRegistry:
        def restrict(self, allowed):
            return self

        def schemas(self):
            return ()

        def execute(self, call, context):
            return ToolResult(call.id, False, {}, pauses_task=True)

    first = TeammateRunner(
        settings=replace(settings, max_teammate_turns=1),
        provider=ScriptedProvider(
            [ModelResponse("", (ToolCall("pause", "inspect_workspace", {}),))]
        ),
        tools=PausingRegistry(),
        repository=repository,
    ).run(run.id)
    resumed = TeammateRunner(
        settings=replace(settings, max_teammate_turns=1),
        provider=ScriptedProvider([ModelResponse('{"should":"not call"}', ())]),
        tools=PausingRegistry(),
        repository=repository,
    ).run(run.id)

    assert first.status is AgentRunStatus.WAITING
    assert first.turns_used == 1 and first.deadline_at
    assert resumed.status is AgentRunStatus.FAILED
    assert resumed.error_category == "agent_turn_limit"


def test_working_run_continues_without_illegal_self_transition(
    settings, db_connection, task, tool_registry
) -> None:
    """A manager re-entry may continue an already-working durable run."""
    repository = TeamRepository(db_connection)
    run = repository.create_run(task.id, AgentRole.KNOWLEDGE, "Inspect workspace")
    repository.transition_run(run.id, AgentRunStatus.WORKING)
    result = TeammateRunner(
        settings=settings,
        provider=ScriptedProvider([ModelResponse('{"ok":true}', ())]),
        tools=tool_registry,
        repository=repository,
    ).run(run.id)
    assert result.status is AgentRunStatus.COMPLETED


def test_concurrent_runners_execute_only_lease_owner_for_working_run(
    settings, tmp_path, tool_registry
) -> None:
    """Removing the durable worker claim lets both runners call the model for one working run."""
    database_path = tmp_path / "concurrent-teammate-runner.sqlite3"
    owner_connection = connect_database(replace(settings, database_path=database_path))
    contender_connection = connect_database(replace(settings, database_path=database_path))
    try:
        project = ProjectRepository(owner_connection).create("Concurrent runner")
        task = TaskRepository(owner_connection).create(project.id, "Complete exactly once")
        owner_repository = TeamRepository(owner_connection)
        contender_repository = TeamRepository(contender_connection)
        run = owner_repository.create_run(task.id, AgentRole.KNOWLEDGE, "Return a handoff")
        owner_repository.transition_run(run.id, AgentRunStatus.WORKING)
        owner_claimed, provider_entered, allow_owner, contender_mutated = (
            Event(),
            Event(),
            Event(),
            Event(),
        )
        claim_owner = owner_repository.claim_worker
        claim_contender = contender_repository.claim_worker

        def owner_claim_worker(*args, **kwargs):
            claimed = claim_owner(*args, **kwargs)
            owner_claimed.set()
            return claimed

        def contender_claim_worker(*args, **kwargs):
            assert owner_claimed.wait(timeout=5)
            return claim_contender(*args, **kwargs)

        def unexpected_contender_mutation(*args, **kwargs):
            del args, kwargs
            contender_mutated.set()
            raise AssertionError("A runner without the lease must not mutate the run")

        owner_repository.claim_worker = owner_claim_worker  # type: ignore[method-assign]
        contender_repository.claim_worker = contender_claim_worker  # type: ignore[method-assign]
        for method in (
            "transition_run",
            "reserve_turn",
            "complete_run",
            "fail_run",
            "append_message",
        ):
            setattr(contender_repository, method, unexpected_contender_mutation)
        provider_lock = Lock()

        class BlockingProvider:
            def __init__(self) -> None:
                self.calls = 0

            def complete(self, request):
                del request
                with provider_lock:
                    self.calls += 1
                provider_entered.set()
                assert allow_owner.wait(timeout=5)
                return ModelResponse('{"owner":true}', ())

        provider = BlockingProvider()
        barrier = Barrier(2)
        outcomes: Queue[AgentRun] = Queue()

        def execute(repository: TeamRepository) -> AgentRun:
            barrier.wait(timeout=5)
            result = TeammateRunner(
                settings=replace(settings, max_teammate_turns=1),
                provider=provider,
                tools=tool_registry,
                repository=repository,
            ).run(run.id)
            outcomes.put(result)
            return result

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = (
                pool.submit(execute, owner_repository),
                pool.submit(execute, contender_repository),
            )
            assert provider_entered.wait(timeout=5)
            busy = outcomes.get(timeout=5)
            held = owner_repository.get_run(run.id)
            assert busy.status is AgentRunStatus.WORKING
            assert held.status is AgentRunStatus.WORKING
            assert held.worker_token is not None
            assert held.turns_used == 1
            assert provider.calls == 1
            assert not contender_mutated.is_set()

            allow_owner.set()
            completed = outcomes.get(timeout=5)
            assert [future.result(timeout=5) for future in futures]

        assert completed.status is AgentRunStatus.COMPLETED
        assert completed.result_json == '{"owner":true}'
        assert provider.calls == 1
        assert not contender_mutated.is_set()
        final = owner_repository.get_run(run.id)
        assert final.status is AgentRunStatus.COMPLETED
        assert final.worker_token is None
    finally:
        contender_connection.close()
        owner_connection.close()


def test_owner_keeps_lease_between_turns_and_blocks_midrun_contender(
    settings, tmp_path, tool_registry
) -> None:
    """Removing the lease between turns lets a contender transition or call the provider mid-run."""
    database_path = tmp_path / "midturn-teammate-runner.sqlite3"
    owner_connection = connect_database(replace(settings, database_path=database_path))
    contender_connection = connect_database(replace(settings, database_path=database_path))
    try:
        project = ProjectRepository(owner_connection).create("Midturn lease")
        task = TaskRepository(owner_connection).create(project.id, "Finish after a tool")
        owner_repository = TeamRepository(owner_connection)
        contender_repository = TeamRepository(contender_connection)
        run = owner_repository.create_run(
            task.id, AgentRole.KNOWLEDGE, "Use one tool then hand off"
        )
        second_started, second_finished, transition_called = Event(), Event(), Event()
        contender_outcomes: Queue[AgentRun] = Queue()

        def unexpected_transition(*args, **kwargs):
            del args, kwargs
            transition_called.set()
            raise AssertionError("A runner without the lease must not transition the run")

        contender_repository.transition_run = unexpected_transition  # type: ignore[method-assign]

        class TwoTurnProvider:
            def __init__(self) -> None:
                self.calls = 0
                self._lock = Lock()

            def complete(self, request):
                del request
                with self._lock:
                    self.calls += 1
                    if self.calls == 1:
                        return ModelResponse("", (ToolCall("inspect", "inspect_workspace", {}),))
                    return ModelResponse('{"completed":"after tool"}', ())

        provider = TwoTurnProvider()

        def run_contender() -> None:
            second_started.set()
            try:
                contender_outcomes.put(
                    TeammateRunner(
                        settings=replace(settings, max_teammate_turns=2),
                        provider=provider,
                        tools=tool_registry,
                        repository=contender_repository,
                    ).run(run.id)
                )
            finally:
                second_finished.set()

        class Registry:
            def restrict(self, allowed):
                del allowed
                return self

            def schemas(self):
                return ()

            def execute(self, call, context):
                del context
                contender = Thread(target=run_contender)
                contender.start()
                assert second_started.wait(timeout=5)
                assert second_finished.wait(timeout=5)
                contender.join(timeout=5)
                assert not contender.is_alive()
                return ToolResult(call.id, True, {"inspected": True})

        completed = TeammateRunner(
            settings=replace(settings, max_teammate_turns=2),
            provider=provider,
            tools=Registry(),
            repository=owner_repository,
        ).run(run.id)

        contender = contender_outcomes.get(timeout=5)
        assert contender.status is AgentRunStatus.WORKING
        assert not transition_called.is_set()
        assert provider.calls == 2
        assert completed.status is AgentRunStatus.COMPLETED
        assert completed.turns_used == 2
        assert completed.worker_token is None
    finally:
        contender_connection.close()
        owner_connection.close()


@pytest.mark.parametrize("text", ('{"value":NaN}', '{"value":Infinity}', '{"value":-Infinity}'))
def test_teammate_rejects_non_finite_json_handoffs(
    settings, db_connection, task, tool_registry, text
) -> None:
    """Non-finite JSON constants cannot enter durable handoff results."""
    repository = TeamRepository(db_connection)
    run = repository.create_run(task.id, AgentRole.KNOWLEDGE, "Find definition")

    failed = TeammateRunner(
        settings=settings,
        provider=ScriptedProvider(
            [ModelResponse(text, ()), ModelResponse(text, ())]
        ),
        tools=tool_registry,
        repository=repository,
    ).run(run.id)

    assert failed.status is AgentRunStatus.FAILED
    assert failed.error_category == "invalid_handoff"
    assert failed.result_json is None
