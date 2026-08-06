from __future__ import annotations

import json

from data_analysis_agent.agent import AgentRunner
from data_analysis_agent.providers import ModelResponse, ScriptedProvider
from data_analysis_agent.team import TeamRepository, TeammateRunner
from data_analysis_agent.team_types import AgentRole
from data_analysis_agent.tools import ToolCall, ToolResult
from data_analysis_agent.tracing import TraceContext, TraceRepository


def test_lead_trace_context_reaches_tool_and_records_safe_artifacts(
    settings, task_repository, task
) -> None:
    traces = TraceRepository(task_repository._connection)
    analysis_run = traces.start_run(task.id)

    class Registry:
        def __init__(self) -> None:
            self.context = None

        def schemas(self):
            return ()

        def execute(self, call, context):
            self.context = context
            return ToolResult(
                call_id=call.id,
                success=True,
                output={"artifact_id": "artifact-safe"},
                diagnostics={"raw_sql": "SELECT secret_sentinel FROM private_table"},
            )

    registry = Registry()
    provider = ScriptedProvider(
        [
            ModelResponse("", (ToolCall("call-1", "inspect_workspace", {}),)),
            ModelResponse("done", ()),
        ]
    )

    AgentRunner(
        settings=settings,
        provider=provider,
        tools=registry,
        tasks=task_repository,
        traces=traces,
    ).run(task.id, TraceContext(analysis_run.id, task.id))

    assert registry.context.run_id == analysis_run.id
    events = traces.events_for_run(analysis_run.id)
    assert [event.event_type for event in events] == [
        "agent.started", "model.completed", "tool.started", "tool.completed",
        "model.completed", "agent.completed",
    ]
    assert events[3].artifact_ids == ("artifact-safe",)
    serialized = json.dumps([event.payload for event in events])
    assert "secret_sentinel" not in serialized
    assert "private_table" not in serialized


def test_teammate_row_persists_parent_trace_correlation(db_connection, task) -> None:
    traces = TraceRepository(db_connection)
    analysis_run = traces.start_run(task.id)
    parent = traces.append(
        analysis_run.id, task.id, "tool.started", "running", {"tool": "spawn_teammate"}
    )
    repository = TeamRepository(db_connection)

    run = repository.create_run(
        task.id,
        AgentRole.SQL,
        "Produce governed evidence",
        trace_context=TraceContext(analysis_run.id, task.id, parent_event_id=parent.id),
    )

    recovered = repository.get_run(run.id)
    assert recovered.trace_run_id == analysis_run.id
    assert recovered.parent_event_id == parent.id


def test_teammate_lifecycle_is_recorded_on_parent_analysis_run(
    db_connection, settings, tool_registry, task
) -> None:
    traces = TraceRepository(db_connection)
    analysis_run = traces.start_run(task.id)
    repository = TeamRepository(db_connection)
    run = repository.create_run(
        task.id,
        AgentRole.KNOWLEDGE,
        "Resolve metric definition",
        trace_context=TraceContext(analysis_run.id, task.id),
    )
    provider = ScriptedProvider([ModelResponse('{"definition":"net revenue"}', ())])

    completed = TeammateRunner(
        settings=settings,
        provider=provider,
        tools=tool_registry,
        repository=repository,
        traces=traces,
    ).run(run.id)

    assert completed.status.value == "completed"
    events = traces.events_for_run(analysis_run.id)
    assert [event.event_type for event in events] == [
        "teammate.started", "teammate.model.completed", "teammate.completed"
    ]
    assert all(event.agent_id == run.id for event in events)
