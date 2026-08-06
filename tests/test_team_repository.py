from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from data_analysis_agent.team import (
    AgentRole,
    AgentRunStatus,
    InvalidAgentRunTransition,
    TeamRepository,
)
from data_analysis_agent.db import migrate
from data_analysis_agent.repositories import ProjectRepository, TaskRepository


def test_repository_persists_independent_teammate_history(db_connection, task) -> None:
    repository = TeamRepository(db_connection)
    sql_run = repository.create_run(task.id, AgentRole.SQL, "Aggregate regional profit")
    report_run = repository.create_run(task.id, AgentRole.REPORT, "Write report")

    repository.append_message(sql_run.id, "assistant", "Preparing a bounded query")

    assert repository.get_run(sql_run.id).status is AgentRunStatus.CREATED
    assert [item.content for item in repository.list_messages(sql_run.id)] == [
        "Preparing a bounded query"
    ]
    assert repository.list_messages(report_run.id) == ()
    assert [item.id for item in repository.list_runs(task.id)] == [sql_run.id, report_run.id]


def test_run_transitions_reject_terminal_restart(db_connection, task) -> None:
    repository = TeamRepository(db_connection)
    run = repository.create_run(task.id, AgentRole.REPORT, "Write report")

    repository.transition_run(run.id, AgentRunStatus.CANCELLED)

    with pytest.raises(InvalidAgentRunTransition):
        repository.transition_run(run.id, AgentRunStatus.WORKING)


def test_active_count_is_serialized_with_concurrent_creates(tmp_path) -> None:
    connection = sqlite3.connect(tmp_path / "team.sqlite3", check_same_thread=False)
    try:
        migrate(connection)
        project = ProjectRepository(connection).create("Concurrent test")
        task = TaskRepository(connection).create(project.id, "Count active runs")
        repository = TeamRepository(connection)

        with ThreadPoolExecutor(max_workers=3) as executor:
            list(
                executor.map(
                    lambda index: repository.create_run(
                        task.id, AgentRole.SQL, f"Query {index}"
                    ),
                    range(3),
                )
            )

        assert repository.active_count(task.id) == 3
    finally:
        connection.close()


def test_request_cancel_is_idempotent_and_does_not_change_terminal_run(db_connection, task) -> None:
    repository = TeamRepository(db_connection)
    active = repository.create_run(task.id, AgentRole.SQL, "Long query")
    completed = repository.create_run(task.id, AgentRole.KNOWLEDGE, "Find definition")
    repository.transition_run(completed.id, AgentRunStatus.WORKING)
    repository.complete_run(completed.id, '{"summary":"Gross profit"}')

    first = repository.request_cancel(active.id)
    second = repository.request_cancel(active.id)
    terminal = repository.request_cancel(completed.id)

    assert first.cancel_requested is True
    assert second.cancel_requested is True
    assert terminal.cancel_requested is False
    assert terminal.status is AgentRunStatus.COMPLETED
    assert repository.cancel_requested(active.id) is True


def test_startup_marks_only_running_and_waiting_runs_interrupted(db_connection, task) -> None:
    repository = TeamRepository(db_connection)
    created = repository.create_run(task.id, AgentRole.KNOWLEDGE, "Remain queued")
    working = repository.create_run(task.id, AgentRole.SQL, "Interrupted query")
    waiting = repository.create_run(task.id, AgentRole.REPORT, "Interrupted report")
    repository.transition_run(working.id, AgentRunStatus.WORKING)
    repository.transition_run(waiting.id, AgentRunStatus.WORKING)
    repository.transition_run(waiting.id, AgentRunStatus.WAITING)

    assert repository.mark_interrupted_runs() == 2
    assert repository.get_run(created.id).status is AgentRunStatus.CREATED
    for run_id in (working.id, waiting.id):
        run = repository.get_run(run_id)
        assert run.status is AgentRunStatus.FAILED
        assert run.error_category == "interrupted"
        assert run.error_message == "Teammate interrupted by service restart"


def test_messages_require_existing_task_run_and_have_stable_order(db_connection, task) -> None:
    repository = TeamRepository(db_connection)
    run = repository.create_run(task.id, AgentRole.SQL, "Order messages")
    first = repository.append_message(run.id, "assistant", "first")
    second = repository.append_message(run.id, "tool", "second")

    assert [message.id for message in repository.list_messages(run.id)] == [first.id, second.id]
    with pytest.raises(Exception):
        repository.create_run("missing-task", AgentRole.SQL, "Fails FK")


def test_reserve_turn_returns_explicit_claim_or_none(db_connection, task) -> None:
    repository = TeamRepository(db_connection)
    run = repository.create_run(task.id, AgentRole.KNOWLEDGE, "Claim one")

    assert repository.claim_worker(run.id, "worker") is not None
    claimed = repository.reserve_turn(run.id, "worker", max_turns=1, timeout_seconds=60)
    rejected = repository.reserve_turn(run.id, "worker", max_turns=1, timeout_seconds=60)

    assert claimed is not None and claimed.turns_used == 1
    assert rejected is None
