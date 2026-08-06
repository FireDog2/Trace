from threading import Event, Thread
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from data_analysis_agent.team import AgentRole, TeamManager, TeamManagerClosed, TeamRepository
from data_analysis_agent.db import migrate
from data_analysis_agent.repositories import ProjectRepository, TaskRepository


def test_manager_persists_run_before_submitting(task, db_connection, settings) -> None:
    observed = []
    team_repository = TeamRepository(db_connection)

    class Runner:
        def run(self, run_id):
            observed.append(run_id)
            return None

    manager = TeamManager(team_repository, settings, lambda role, event: Runner())
    run = manager.spawn(task.id, AgentRole.SQL, "Query")
    manager.await_runs((run.id,), 1)
    manager.shutdown()
    assert observed == [run.id]


def test_shutdown_rejects_spawn_without_creating_a_run(task, db_connection, settings) -> None:
    repository = TeamRepository(db_connection)
    manager = TeamManager(repository, settings, lambda role, event: None)
    manager.shutdown()

    with pytest.raises(TeamManagerClosed):
        manager.spawn(task.id, AgentRole.SQL, "Must not persist")
    assert repository.list_runs(task.id) == ()


def test_spawn_shutdown_are_linearized_before_repository_create(tmp_path, settings) -> None:
    connection = sqlite3.connect(tmp_path / "race.sqlite", check_same_thread=False)
    migrate(connection)
    project = ProjectRepository(connection).create("Race")
    task = TaskRepository(connection).create(project.id, "Race")
    repository = TeamRepository(connection)
    from data_analysis_agent.team import AgentRunStatus
    class CooperativeRunner:
        def __init__(self, event):
            self.event = event
        def run(self, run_id):
            assert self.event.wait(2)
            return repository.transition_run(run_id, AgentRunStatus.CANCELLED)
    manager = TeamManager(repository, settings, lambda role, event: CooperativeRunner(event))
    entered, release = Event(), Event()
    original = repository.create_run_if_capacity

    def blocked_create(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return original(*args, **kwargs)

    repository.create_run_if_capacity = blocked_create  # type: ignore[method-assign]
    outcome = []
    thread = Thread(target=lambda: outcome.append(manager.spawn(task.id, AgentRole.SQL, "Race")))
    thread.start()
    assert entered.wait(2)
    shutdown_returned = Event()
    shutdown_thread = Thread(target=lambda: (manager.shutdown(), shutdown_returned.set()))
    shutdown_thread.start()
    # Regression guard: the pre-linearization implementation returned here.
    assert not shutdown_returned.wait(0.05)
    release.set()
    thread.join(2)
    shutdown_thread.join(2)
    assert shutdown_returned.is_set()

    try:
        assert len(repository.list_runs(task.id)) == 1
        assert repository.get_run(outcome[0].id).status.value == "cancelled"
    finally:
        connection.close()


def test_submit_failure_is_terminal_and_releases_capacity(task, db_connection, settings) -> None:
    """A rejected executor submission cannot strand a durable active row."""
    repository = TeamRepository(db_connection)
    manager = TeamManager(repository, settings, lambda role, event: None)

    class RejectingExecutor:
        def submit(self, *args, **kwargs):
            raise RuntimeError("executor rejected work")

        def shutdown(self, **kwargs):
            return None

    manager._executor = RejectingExecutor()  # type: ignore[assignment]
    rejected = manager.spawn(task.id, AgentRole.SQL, "Will not run")

    assert repository.get_run(rejected.id).status.value == "failed"
    manager._executor = ThreadPoolExecutor(max_workers=1)  # type: ignore[assignment]
    next_run = manager.spawn(task.id, AgentRole.SQL, "Capacity is available")
    assert next_run.status.value == "created"
    manager.shutdown()
