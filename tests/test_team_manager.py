import sqlite3
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier, Event, RLock, Thread, current_thread

import pytest

from data_analysis_agent.db import migrate
from data_analysis_agent.repositories import ProjectRepository, TaskRepository
from data_analysis_agent.team import (
    AgentRole,
    AgentRunStatus,
    TeamCapacityError,
    TeamManager,
    TeamManagerClosed,
    TeamRepository,
)


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
    shutdown_attempted, executor_shutdown_called = Event(), Event()

    class TrackingLock:
        def __init__(self) -> None:
            self._inner = RLock()

        def __enter__(self):
            if current_thread().name == "linearized-shutdown":
                shutdown_attempted.set()
            self._inner.acquire()
            return self

        def __exit__(self, *args):
            self._inner.release()

    class TrackingExecutor:
        def __init__(self, inner) -> None:
            self._inner = inner

        def submit(self, *args, **kwargs):
            return self._inner.submit(*args, **kwargs)

        def shutdown(self, **kwargs):
            executor_shutdown_called.set()
            return self._inner.shutdown(**kwargs)

    manager._lock = TrackingLock()  # type: ignore[assignment]
    manager._executor = TrackingExecutor(manager._executor)  # type: ignore[assignment]
    outcome = []
    thread = Thread(target=lambda: outcome.append(manager.spawn(task.id, AgentRole.SQL, "Race")))
    thread.start()
    assert entered.wait(2)
    shutdown_returned = Event()
    shutdown_thread = Thread(
        name="linearized-shutdown",
        target=lambda: (manager.shutdown(), shutdown_returned.set()),
    )
    shutdown_thread.start()
    assert shutdown_attempted.wait(2)
    # The shutdown thread has attempted the manager lock but cannot reach the executor
    # until the in-flight spawn has completed its durable creation and registration.
    assert not executor_shutdown_called.wait(1)
    release.set()
    thread.join(2)
    shutdown_thread.join(2)
    assert shutdown_returned.is_set()

    try:
        assert len(repository.list_runs(task.id)) == 1
        assert repository.get_run(outcome[0].id).status.value == "cancelled"
    finally:
        connection.close()


def test_already_completed_future_cleans_manager_handles_and_keeps_terminal_run(
    task, db_connection, settings
) -> None:
    repository = TeamRepository(db_connection)

    class CompletingRunner:
        def run(self, run_id):
            repository.transition_run(run_id, AgentRunStatus.WORKING)
            return repository.complete_run(run_id, '{"summary":"done"}')

    class InlineExecutor:
        def submit(self, callback, *args):
            future = Future()
            try:
                future.set_result(callback(*args))
            except BaseException as exc:
                future.set_exception(exc)
            return future

        def shutdown(self, **kwargs):
            return None

    manager = TeamManager(repository, settings, lambda role, event: CompletingRunner())
    manager._executor = InlineExecutor()  # type: ignore[assignment]
    run = manager.spawn(task.id, AgentRole.SQL, "Complete inline")

    assert repository.get_run(run.id).status is AgentRunStatus.COMPLETED
    assert manager._futures == {}
    assert manager._cancel_events == {}
    manager.shutdown()


def test_await_returns_active_row_at_timeout_then_observes_later_completion(
    tmp_path, settings
) -> None:
    connection = sqlite3.connect(tmp_path / "await.sqlite", check_same_thread=False)
    migrate(connection)
    project = ProjectRepository(connection).create("Await lifecycle")
    task = TaskRepository(connection).create(project.id, "Await lifecycle")
    repository = TeamRepository(connection)
    release, started = Event(), Event()

    class Runner:
        def run(self, run_id):
            repository.transition_run(run_id, AgentRunStatus.WORKING)
            started.set()
            assert release.wait(2)
            return repository.complete_run(run_id, '{"summary":"done"}')

    manager = TeamManager(repository, settings, lambda role, event: Runner())
    run = manager.spawn(task.id, AgentRole.SQL, "Wait once")
    assert started.wait(2)
    assert manager.await_runs((run.id,), 1)[0].status in {
        AgentRunStatus.CREATED,
        AgentRunStatus.WORKING,
    }
    release.set()
    assert manager.await_runs((run.id,), 1)[0].status is AgentRunStatus.COMPLETED
    manager.shutdown()
    connection.close()


def test_manager_resumes_existing_waiting_run(tmp_path, settings) -> None:
    connection = sqlite3.connect(tmp_path / "resume-waiting.sqlite", check_same_thread=False)
    migrate(connection)
    project = ProjectRepository(connection).create("Resume waiting")
    task = TaskRepository(connection).create(project.id, "Resume waiting")
    repository = TeamRepository(connection)
    run = repository.create_run(task.id, AgentRole.SQL, "Resume after approval")
    repository.transition_run(run.id, AgentRunStatus.WORKING)
    repository.transition_run(run.id, AgentRunStatus.WAITING)

    class Runner:
        def run(self, run_id):
            repository.transition_run(run_id, AgentRunStatus.WORKING)
            return repository.complete_run(run_id, '{"summary":"approved"}')

    manager = TeamManager(repository, settings, lambda role, event: Runner())
    resumed = manager.resume_waiting(task.id)
    completed = manager.await_runs((run.id,), 1)[0]

    assert [item.id for item in resumed] == [run.id]
    assert completed.status is AgentRunStatus.COMPLETED
    manager.shutdown()
    connection.close()


def test_manager_cancels_waiting_run_without_live_future(task, db_connection, settings) -> None:
    repository = TeamRepository(db_connection)
    run = repository.create_run(task.id, AgentRole.SQL, "Cancel approval wait")
    repository.transition_run(run.id, AgentRunStatus.WORKING)
    repository.transition_run(run.id, AgentRunStatus.WAITING)
    manager = TeamManager(repository, settings, lambda role, event: None)

    cancelled = manager.cancel(run.id)

    assert cancelled.status is AgentRunStatus.CANCELLED
    assert cancelled.cancel_requested is True
    manager.shutdown()


def test_two_managers_preserve_database_active_capacity_under_concurrent_spawns(
    tmp_path, settings
) -> None:
    """Separate app connections must not turn the per-task cap into a check-then-insert race."""
    database = tmp_path / "shared-capacity.sqlite"
    bootstrap = sqlite3.connect(database, timeout=5, check_same_thread=False)
    migrate(bootstrap)
    project = ProjectRepository(bootstrap).create("Concurrent capacity")
    task = TaskRepository(bootstrap).create(project.id, "Race for capacity")
    bootstrap.close()

    settings = replace(settings, max_active_teammates=1)
    for _ in range(20):
        left = sqlite3.connect(database, timeout=5, check_same_thread=False)
        right = sqlite3.connect(database, timeout=5, check_same_thread=False)
        left.execute("PRAGMA journal_mode = WAL")
        right.execute("PRAGMA journal_mode = WAL")
        left_repository, right_repository = TeamRepository(left), TeamRepository(right)
        release = Event()

        def manager_for(repository):
            class Runner:
                def run(self, run_id):
                    repository.transition_run(run_id, AgentRunStatus.WORKING)
                    assert release.wait(2)
                    return repository.transition_run(run_id, AgentRunStatus.CANCELLED)

            return TeamManager(repository, settings, lambda role, event: Runner())

        managers = (manager_for(left_repository), manager_for(right_repository))
        barrier = Barrier(3)
        outcomes: list[tuple[TeamManager, object]] = []

        def spawn(manager):
            barrier.wait()
            try:
                outcomes.append((manager, manager.spawn(task.id, AgentRole.SQL, "Competing run")))
            except BaseException as exc:
                outcomes.append((manager, exc))

        threads = tuple(Thread(target=spawn, args=(manager,)) for manager in managers)
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(5)
        assert all(not thread.is_alive() for thread in threads)
        assert len(outcomes) == 2
        assert sum(not isinstance(item, BaseException) for _, item in outcomes) == 1
        assert sum(isinstance(item, TeamCapacityError) for _, item in outcomes) == 1
        assert left_repository.active_count(task.id) <= 1

        release.set()
        for manager, item in outcomes:
            if not isinstance(item, BaseException):
                assert manager.await_runs((item.id,), 2)[0].status is AgentRunStatus.CANCELLED
        for manager in managers:
            manager.shutdown()
        left.close()
        right.close()


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


@pytest.mark.parametrize("failure", ("factory", "runner"))
def test_manager_converts_worker_construction_and_run_failures_to_terminal_rows(
    tmp_path, settings, failure
) -> None:
    connection = sqlite3.connect(tmp_path / "worker-failure.sqlite", check_same_thread=False)
    migrate(connection)
    project = ProjectRepository(connection).create("Worker failure")
    task = TaskRepository(connection).create(project.id, "Failure")
    repository = TeamRepository(connection)

    def factory(role, event):
        del role, event
        if failure == "factory":
            raise RuntimeError("factory failure")

        class Runner:
            def run(self, run_id):
                del run_id
                raise RuntimeError("runner failure")

        return Runner()

    manager = TeamManager(repository, settings, factory)
    run = manager.spawn(task.id, AgentRole.SQL, "Fails safely")
    terminal = manager.await_runs((run.id,), 1)[0]
    assert terminal.status is AgentRunStatus.FAILED
    assert terminal.error_category == "provider_error"
    manager.shutdown()
    connection.close()


def test_shutdown_cancels_queued_work_and_running_work_cooperates(tmp_path, settings) -> None:
    connection = sqlite3.connect(tmp_path / "shutdown.sqlite", check_same_thread=False)
    migrate(connection)
    project = ProjectRepository(connection).create("Shutdown")
    task = TaskRepository(connection).create(project.id, "Shutdown")
    repository = TeamRepository(connection)
    started, release = Event(), Event()
    constrained = replace(settings, max_active_teammates=2)

    class Runner:
        def __init__(self, event):
            self.event = event

        def run(self, run_id):
            started.set()
            assert release.wait(2)
            return repository.transition_run(run_id, AgentRunStatus.CANCELLED)

    manager = TeamManager(repository, constrained, lambda role, event: Runner(event))
    manager._executor.shutdown(wait=False, cancel_futures=True)
    manager._executor = ThreadPoolExecutor(max_workers=1)  # type: ignore[assignment]
    first = manager.spawn(task.id, AgentRole.SQL, "Running")
    assert started.wait(2)
    second = manager.spawn(task.id, AgentRole.REPORT, "Queued")
    manager.shutdown()
    release.set()
    assert manager.await_runs((first.id, second.id), 1)[0].status is AgentRunStatus.CANCELLED
    assert repository.get_run(second.id).status is AgentRunStatus.CANCELLED
    manager.shutdown()
    with pytest.raises(TeamManagerClosed):
        manager.spawn(task.id, AgentRole.SQL, "Closed")
    connection.close()
