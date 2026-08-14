from __future__ import annotations

import json

from data_analysis_agent.tracing import TraceRepository, redact_payload


def test_trace_repository_orders_events_for_a_run(db_connection, task) -> None:
    repository = TraceRepository(db_connection)
    run = repository.start_run(task.id)
    parent = repository.append(run.id, task.id, "agent.started", "running", {"role": "lead"})
    repository.append(
        run.id,
        task.id,
        "tool.completed",
        "completed",
        {"tool": "query_database"},
        parent_event_id=parent.id,
    )
    assert [event.sequence for event in repository.events_for_run(run.id)] == [1, 2]
    repository.finish_run(run.id, "completed")
    assert repository.accept_run(run.id).accepted is True


def test_redact_payload_removes_secrets_and_bounds_values() -> None:
    value = redact_payload(
        {
            "dsn": "postgresql://analyst:secret@db",
            "nested": {"Authorization": "Bearer abc"},
            "text": "x" * 5000,
            "items": list(range(101)),
        }
    )
    serialized = json.dumps(value)
    assert "secret" not in serialized and "abc" not in serialized
    assert value["dsn"] == "[REDACTED]"
    assert len(value["text"]) <= 4100 and len(value["items"]) == 100
