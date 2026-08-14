from __future__ import annotations

import json

from data_analysis_agent.context import (
    ContextCheckpointRepository,
    ContextManager,
    ContextMessage,
)


def _message(index: int, role: str, content: str, safe: str | None = None) -> ContextMessage:
    return ContextMessage(str(index), role, content, safe if safe is not None else content)


def test_compaction_persists_projection_without_mutating_transcript(
    db_connection,
) -> None:
    repository = ContextCheckpointRepository(db_connection)
    manager = ContextManager(
        repository,
        max_tokens=1_200,
        target_tokens=1_024,
        recent_messages=4,
        max_tool_result_chars=1_000,
    )
    messages = tuple(
        _message(
            index,
            "user" if index % 2 == 0 else "assistant",
            f"turn-{index} " + "x" * 900,
        )
        for index in range(12)
    )

    projection = manager.project(
        owner_type="task",
        owner_id="task-1",
        system="system",
        objective="objective",
        messages=messages,
        tools=(),
    )

    assert projection.compacted is True
    assert projection.reason == "auto"
    assert projection.projected_tokens < projection.original_tokens
    assert projection.messages[0].role == "system"
    assert "Session checkpoint" in projection.messages[0].content
    assert messages[0].content.startswith("turn-0")
    checkpoint = repository.latest("task", "task-1")
    assert checkpoint is not None
    assert checkpoint.through_message_id == messages[-5].id


def test_auto_compaction_handles_a_short_but_oversized_history(db_connection) -> None:
    repository = ContextCheckpointRepository(db_connection)
    manager = ContextManager(
        repository,
        max_tokens=1_200,
        target_tokens=1_024,
        recent_messages=12,
        max_tool_result_chars=1_000,
    )
    messages = (
        _message(0, "user", "constraint " + "x" * 3_000),
        _message(1, "assistant", "finding " + "y" * 3_000),
        _message(2, "user", "follow-up " + "z" * 3_000),
    )

    projection = manager.project(
        owner_type="task",
        owner_id="task-short",
        system="system",
        objective="objective",
        messages=messages,
        tools=(),
    )

    assert projection.reason == "auto"
    assert repository.latest("task", "task-short") is not None
    assert projection.projected_tokens < projection.original_tokens


def test_compaction_preserves_recent_tool_protocol_and_uses_safe_checkpoint(
    db_connection,
) -> None:
    repository = ContextCheckpointRepository(db_connection)
    manager = ContextManager(
        repository,
        max_tokens=1_200,
        target_tokens=1_024,
        recent_messages=2,
        max_tool_result_chars=1_000,
    )
    old_secret = "customer-secret@example.test"
    messages = (
        _message(0, "user", "x" * 6_000),
        _message(1, "assistant", old_secret, "safe prior analysis"),
        _message(
            2,
            "assistant_tool_call",
            json.dumps(
                {"tool_calls": [{"id": "c1", "name": "inspect_workspace", "arguments": {}}]}
            ),
        ),
        _message(
            3,
            "tool",
            json.dumps({"call_id": "c1", "success": True, "output": {"artifact_id": "a1"}}),
        ),
    )

    projection = manager.project(
        owner_type="agent",
        owner_id="agent-1",
        system="system",
        objective="objective",
        messages=messages,
        tools=(),
    )

    assert [message.role for message in projection.messages[-2:]] == [
        "assistant_tool_call",
        "tool",
    ]
    checkpoint = repository.latest("agent", "agent-1")
    assert checkpoint is not None
    assert old_secret not in checkpoint.summary
    assert "safe prior analysis" in checkpoint.summary


def test_large_tool_result_is_budgeted_as_valid_protocol_json(db_connection) -> None:
    manager = ContextManager(
        ContextCheckpointRepository(db_connection),
        max_tokens=32_000,
        target_tokens=24_000,
        recent_messages=4,
        max_tool_result_chars=1_000,
    )
    content = json.dumps(
        {
            "call_id": "c1",
            "success": True,
            "output": {"artifact_id": "a1", "preview": "x" * 5_000},
        }
    )
    projection = manager.project(
        owner_type="task",
        owner_id="task-1",
        system="system",
        objective="objective",
        messages=(
            _message(
                0,
                "assistant_tool_call",
                json.dumps({"tool_calls": [{"id": "c1", "name": "query_data", "arguments": {}}]}),
            ),
            _message(1, "tool", content),
        ),
        tools=(),
    )

    result = json.loads(projection.messages[-1].content)
    assert result["call_id"] == "c1"
    assert result["output"] == {"compacted": True, "artifact_id": "a1"}


def test_context_checkpoints_are_deleted_with_their_owner(db_connection, task) -> None:
    repository = ContextCheckpointRepository(db_connection)
    db_connection.execute(
        "INSERT INTO agent_runs(id,task_id,role,objective,status,cancel_requested,created_at) "
        "VALUES ('agent-1',?,'knowledge','Resolve','created',0,'2026-01-01T00:00:00+00:00')",
        (task.id,),
    )
    for owner_type, owner_id in (("task", task.id), ("agent", "agent-1")):
        repository.create(
            owner_type=owner_type,
            owner_id=owner_id,
            through_message_id="message-1",
            summary="checkpoint",
            source_message_count=1,
            before_tokens=2_000,
            after_tokens=1_000,
            reason="auto",
        )

    db_connection.execute("DELETE FROM agent_runs WHERE id='agent-1'")
    db_connection.commit()
    assert repository.latest("agent", "agent-1") is None
    assert repository.latest("task", task.id) is not None

    db_connection.execute("DELETE FROM tasks WHERE id=?", (task.id,))
    db_connection.commit()
    assert repository.latest("task", task.id) is None
