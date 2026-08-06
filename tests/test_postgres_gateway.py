from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from data_analysis_agent.data_sources import (
    AccessLevel,
    DataSource,
    DataSourceCredentials,
    DataSourceRepository,
    PolicySnapshot,
)
from data_analysis_agent.db import migrate
from data_analysis_agent.domain import ArtifactType, TaskStatus
from data_analysis_agent.permissions import ApprovalRepository, ToolAction
from data_analysis_agent.postgres import (
    ExplainRiskPolicy,
    PsycopgDriver,
    SqlSafetyGateway,
)
from data_analysis_agent.repositories import (
    ArtifactRepository,
    ProjectRepository,
    TaskRepository,
)
from data_analysis_agent.settings import Settings
from data_analysis_agent.sql_safety import SqlAstValidator
from data_analysis_agent.workspace import WorkspaceService


def _create_source(source_repository, project_id: str):
    source = source_repository.create(
        project_id,
        "Warehouse",
        "postgresql://analyst:secret@localhost/portfolio",
    )
    source_repository.replace_policies(
        source.id,
        {
            ("orders", None): AccessLevel.ALLOWED,
            ("orders", "customer_email"): AccessLevel.APPROVAL_REQUIRED,
        },
    )
    return source


def test_restricted_query_uses_exact_one_time_approval_and_persists_csv(
    sql_gateway,
    source_repository,
    approval_repository,
    artifact_repository,
    task_repository,
    fake_driver,
    task,
) -> None:
    source = _create_source(source_repository, task.project_id)
    sql = "SELECT region, customer_email FROM orders"

    first = sql_gateway.execute(task.id, task.project_id, source.id, sql)

    assert first.status == "approval_required"
    assert first.approval_id is not None
    approval = approval_repository.get(first.approval_id)
    assert approval.action == ToolAction(
        "query_database",
        {
            "data_source_id": source.id,
            "sql_hash": first.query_hash,
            "policy_version": 2,
        },
    )
    assert task_repository.get(task.id).status is TaskStatus.NEEDS_INPUT
    event = task_repository.events_after(task.id, 0)[-1]
    assert event.event_type == "approval.requested"
    assert event.payload == {
        "approval_id": first.approval_id,
        "sql_hash": first.query_hash,
        "policy_version": 2,
        "reason": "Restricted fields require one-time approval",
    }

    approval_repository.decide(first.approval_id, approved=True)
    second = sql_gateway.execute(task.id, task.project_id, source.id, sql)

    assert second.status == "succeeded"
    assert second.row_count == 1
    assert second.preview == ({"region": "East", "amount": 10},)
    artifact = artifact_repository.get(second.artifact_id)
    assert artifact.artifact_type is ArtifactType.DERIVED_DATASET
    assert artifact.path.name == f"postgres-query-{second.query_hash[:12]}.csv"
    assert artifact.path.read_text(encoding="utf-8") == (
        "region,amount\nEast,10\n"
    )
    assert fake_driver.explained_sql[-1][0] == (
        "SELECT region, customer_email FROM orders"
    )
    assert fake_driver.queried_sql[-1] == (
        "SELECT region, customer_email FROM orders",
        8_000,
        1_000,
    )

    third = sql_gateway.execute(task.id, task.project_id, source.id, sql)

    assert third.status == "approval_required"
    assert third.approval_id != first.approval_id
    listed = approval_repository.list_for_task(task.id)
    assert tuple(item.status for item in listed) == ("consumed", "pending")
    assert tuple(item.id for item in listed) == (
        first.approval_id,
        third.approval_id,
    )


def test_changed_sql_hash_cannot_consume_an_existing_approval(
    sql_gateway,
    source_repository,
    approval_repository,
    task,
) -> None:
    source = _create_source(source_repository, task.project_id)
    first = sql_gateway.execute(
        task.id,
        task.project_id,
        source.id,
        "SELECT region, customer_email FROM orders",
    )
    approval_repository.decide(first.approval_id, approved=True)

    changed = sql_gateway.execute(
        task.id,
        task.project_id,
        source.id,
        "SELECT customer_email FROM orders",
    )

    assert changed.status == "approval_required"
    assert changed.query_hash != first.query_hash
    assert approval_repository.get(first.approval_id).status == "approved"


def test_changed_policy_version_cannot_consume_an_existing_approval(
    sql_gateway,
    source_repository,
    approval_repository,
    task,
) -> None:
    source = _create_source(source_repository, task.project_id)
    sql = "SELECT customer_email FROM orders"
    first = sql_gateway.execute(task.id, task.project_id, source.id, sql)
    approval_repository.decide(first.approval_id, approved=True)
    source_repository.replace_policies(
        source.id,
        {
            ("orders", None): AccessLevel.ALLOWED,
            ("orders", "customer_email"): AccessLevel.APPROVAL_REQUIRED,
        },
    )

    changed = sql_gateway.execute(task.id, task.project_id, source.id, sql)

    assert changed.status == "approval_required"
    assert approval_repository.get(changed.approval_id).action.arguments[
        "policy_version"
    ] == 3
    assert approval_repository.get(first.approval_id).status == "approved"


def test_policy_change_during_explain_does_not_consume_approval(
    sql_gateway,
    source_repository,
    approval_repository,
    fake_driver,
    task,
) -> None:
    source = _create_source(source_repository, task.project_id)
    sql = "SELECT customer_email FROM orders"
    first = sql_gateway.execute(task.id, task.project_id, source.id, sql)
    approval_repository.decide(first.approval_id, approved=True)
    fake_driver.on_explain = lambda: source_repository.replace_policies(
        source.id,
        {("orders", None): AccessLevel.DENIED},
    )

    changed = sql_gateway.execute(task.id, task.project_id, source.id, sql)

    assert changed.status == "denied"
    assert changed.error_category == "policy_changed"
    assert approval_repository.get(first.approval_id).status == "approved"
    assert fake_driver.queried_sql == []


def test_policy_change_during_explain_prevents_allowed_query_execution(
    sql_gateway,
    source_repository,
    fake_driver,
    task,
) -> None:
    source = _create_source(source_repository, task.project_id)
    fake_driver.on_explain = lambda: source_repository.replace_policies(
        source.id,
        {("orders", None): AccessLevel.DENIED},
    )

    changed = sql_gateway.execute(
        task.id,
        task.project_id,
        source.id,
        "SELECT region FROM orders",
    )

    assert changed.status == "denied"
    assert changed.error_category == "policy_changed"
    assert fake_driver.queried_sql == []


def test_repeated_pending_query_reuses_the_existing_approval(
    sql_gateway,
    source_repository,
    approval_repository,
    task,
) -> None:
    source = _create_source(source_repository, task.project_id)
    sql = "SELECT customer_email FROM orders"
    first = sql_gateway.execute(task.id, task.project_id, source.id, sql)

    repeated = sql_gateway.execute(task.id, task.project_id, source.id, sql)

    assert repeated.status == "approval_required"
    assert repeated.approval_id == first.approval_id
    assert tuple(item.id for item in approval_repository.list_for_task(task.id)) == (
        first.approval_id,
    )


def test_rejected_approval_denies_the_exact_bound_query(
    sql_gateway,
    source_repository,
    approval_repository,
    task,
) -> None:
    source = _create_source(source_repository, task.project_id)
    sql = "SELECT customer_email FROM orders"
    first = sql_gateway.execute(task.id, task.project_id, source.id, sql)
    approval_repository.decide(first.approval_id, approved=False)

    rejected = sql_gateway.execute(task.id, task.project_id, source.id, sql)

    assert rejected.status == "denied"
    assert rejected.error_category == "approval_rejected"
    assert tuple(item.id for item in approval_repository.list_for_task(task.id)) == (
        first.approval_id,
    )


def test_denied_policy_never_creates_approval_or_queries_database(
    sql_gateway,
    source_repository,
    approval_repository,
    fake_driver,
    task,
) -> None:
    source = _create_source(source_repository, task.project_id)
    source_repository.replace_policies(
        source.id,
        {("orders", None): AccessLevel.DENIED},
    )

    outcome = sql_gateway.execute(
        task.id,
        task.project_id,
        source.id,
        "SELECT region FROM orders",
    )

    assert outcome.status == "denied"
    assert outcome.error_category == "policy_denied"
    assert approval_repository.list_for_task(task.id) == ()
    assert fake_driver.explained_sql == []
    assert fake_driver.queried_sql == []


def test_timeout_is_structured_retryable_and_redacts_credentials(
    sql_gateway,
    source_repository,
    fake_driver,
    task,
) -> None:
    source = _create_source(source_repository, task.project_id)
    fake_driver.query_error = TimeoutError(
        "statement timeout on postgresql://analyst:secret@localhost/portfolio"
    )

    outcome = sql_gateway.execute(
        task.id,
        task.project_id,
        source.id,
        "SELECT region FROM orders",
    )

    assert outcome.status == "retryable_error"
    assert outcome.error_category == "query_timeout"
    assert outcome.retryable is True
    assert "secret" not in repr(outcome)
    assert "postgresql://" not in repr(outcome)


def test_result_byte_limit_returns_structured_error_without_artifact(
    sql_gateway,
    source_repository,
    artifact_repository,
    fake_driver,
    task,
) -> None:
    source = _create_source(source_repository, task.project_id)
    sql_gateway.settings = replace(sql_gateway.settings, postgres_max_result_bytes=12)
    fake_driver.columns = ("region",)
    fake_driver.rows = (("a-result-that-is-too-large",),)

    outcome = sql_gateway.execute(
        task.id,
        task.project_id,
        source.id,
        "SELECT region FROM orders",
    )

    assert outcome.status == "retryable_error"
    assert outcome.error_category == "result_limit"
    assert outcome.retryable is True
    assert artifact_repository.list_for_task(task.id) == ()


def test_artifact_write_error_is_structured_and_removes_reserved_path(
    sql_gateway,
    source_repository,
    artifact_repository,
    workspace,
    task,
    monkeypatch,
) -> None:
    source = _create_source(source_repository, task.project_id)
    original_write_bytes = Path.write_bytes

    def fail_query_artifact_write(path: Path, data: bytes) -> int:
        if path.name.startswith("postgres-query-"):
            assert path.exists()
            raise OSError("injected disk write failure")
        return original_write_bytes(path, data)

    monkeypatch.setattr(Path, "write_bytes", fail_query_artifact_write)

    outcome = sql_gateway.execute(
        task.id,
        task.project_id,
        source.id,
        "SELECT region FROM orders",
    )

    assert outcome.status == "retryable_error"
    assert outcome.error_category == "artifact_write_error"
    assert outcome.retryable is True
    assert artifact_repository.list_for_task(task.id) == ()
    derived = workspace.resolve_inside(task.project_id, "derived")
    assert tuple(derived.glob("postgres-query-*.csv")) == ()


def test_query_budget_stops_before_an_extra_database_query(
    db_connection,
    settings,
    source_repository,
    approval_repository,
    artifact_repository,
    workspace,
    task_repository,
    fake_driver,
    task,
) -> None:
    source = _create_source(source_repository, task.project_id)
    limited = SqlSafetyGateway(
        connection=db_connection,
        settings=replace(settings, postgres_max_queries_per_task=2),
        sources=source_repository,
        validator=SqlAstValidator(),
        approvals=approval_repository,
        artifacts=artifact_repository,
        workspace=workspace,
        tasks=task_repository,
        driver=fake_driver,
    )

    first = limited.execute(
        task.id, task.project_id, source.id, "SELECT region FROM orders"
    )
    second = limited.execute(
        task.id,
        task.project_id,
        source.id,
        "SELECT amount FROM orders",
    )
    exhausted = limited.execute(
        task.id,
        task.project_id,
        source.id,
        "SELECT region, amount FROM orders",
    )

    assert first.status == "succeeded"
    assert second.status == "succeeded"
    assert exhausted.status == "denied"
    assert exhausted.error_category == "query_budget_exhausted"
    assert exhausted.retryable is False
    assert db_connection.execute(
        "SELECT attempts FROM task_query_budget WHERE task_id = ?", (task.id,)
    ).fetchone()[0] == 2
    assert all(
        sql != "SELECT region, amount FROM orders"
        for sql, _, _ in fake_driver.queried_sql
    )


class StaticSourceRepository:
    def __init__(self, source: DataSource, policy: PolicySnapshot) -> None:
        self.source = source
        self.policy = policy

    def get_public(self, data_source_id: str) -> DataSource:
        return self.source

    def credentials(self, data_source_id: str) -> DataSourceCredentials:
        return DataSourceCredentials(self.source.id, "postgresql://localhost/test")

    def policy_snapshot(self, data_source_id: str) -> PolicySnapshot:
        return self.policy


class CoordinatedApprovalRepository:
    def __init__(
        self,
        repository: ApprovalRepository,
        barrier: threading.Barrier,
    ) -> None:
        self.repository = repository
        self.barrier = barrier

    def list_for_task(self, task_id: str):
        approvals = self.repository.list_for_task(task_id)
        if not approvals:
            self.barrier.wait(timeout=5)
        return approvals

    def resolve_once(self, task_id: str, action: ToolAction, reason: str):
        self.barrier.wait(timeout=5)
        return self.repository.resolve_once(task_id, action, reason)

    def __getattr__(self, name: str):
        return getattr(self.repository, name)


class CoordinatedPathGateway(SqlSafetyGateway):
    def __init__(self, *args, path_barrier: threading.Barrier, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.path_barrier = path_barrier

    def _query_artifact_path(self, project_id: str, sql_hash: str):
        path = super()._query_artifact_path(project_id, sql_hash)
        self.path_barrier.wait(timeout=5)
        return path


class FailSecondRegistrationRepository:
    def __init__(self, repository: ArtifactRepository) -> None:
        self.repository = repository
        self._lock = threading.Lock()
        self._attempt = 0

    def register(self, **kwargs):
        with self._lock:
            self._attempt += 1
            if self._attempt == 2:
                raise RuntimeError("Injected artifact registration failure")
            return self.repository.register(**kwargs)

    def __getattr__(self, name: str):
        return getattr(self.repository, name)


def _build_threaded_gateway(tmp_path, fake_driver, rules):
    database_path = tmp_path / "concurrent-gateway.sqlite3"
    connection = sqlite3.connect(database_path, check_same_thread=False, timeout=5)
    migrate(connection)
    settings = Settings(data_dir=tmp_path / "app-data", database_path=database_path)
    workspace = WorkspaceService(settings)
    project = ProjectRepository(connection).create("Concurrent gateway")
    workspace.create_project(project.id)
    tasks = TaskRepository(connection)
    task = tasks.create(project.id, "Run governed PostgreSQL")
    source = DataSource(
        id="source-concurrent",
        project_id=project.id,
        name="Warehouse",
        dialect="postgres",
        policy_version=1,
        created_at="now",
        updated_at="now",
    )
    sources = StaticSourceRepository(
        source,
        PolicySnapshot(
            data_source_id=source.id,
            version=1,
            rules=rules,
        ),
    )
    approvals = ApprovalRepository(connection)
    return (
        connection,
        settings,
        workspace,
        tasks,
        task,
        source,
        sources,
        approvals,
    )


class PassthroughProtector:
    def protect(self, value: str) -> bytes:
        return value.encode()

    def unprotect(self, value: bytes) -> str:
        return value.decode()


def _build_real_gateway(tmp_path, fake_driver):
    database_path = tmp_path / "real-gateway.sqlite3"
    connection = sqlite3.connect(
        database_path,
        check_same_thread=False,
        timeout=5,
    )
    migrate(connection)
    settings = Settings(data_dir=tmp_path / "app-data", database_path=database_path)
    workspace = WorkspaceService(settings)
    project = ProjectRepository(connection).create("Real gateway")
    workspace.create_project(project.id)
    tasks = TaskRepository(connection)
    task = tasks.create(project.id, "Run governed PostgreSQL")
    sources = DataSourceRepository(connection, PassthroughProtector())
    source = _create_source(sources, project.id)
    approvals = ApprovalRepository(connection)
    artifacts = ArtifactRepository(connection, workspace)
    gateway = SqlSafetyGateway(
        connection=connection,
        settings=settings,
        sources=sources,
        validator=SqlAstValidator(),
        approvals=approvals,
        artifacts=artifacts,
        workspace=workspace,
        tasks=tasks,
        driver=fake_driver,
    )
    return SimpleNamespace(
        connection=connection,
        database_path=database_path,
        settings=settings,
        workspace=workspace,
        project=project,
        tasks=tasks,
        task=task,
        sources=sources,
        source=source,
        approvals=approvals,
        artifacts=artifacts,
        gateway=gateway,
    )


class PolicyChangingApprovalRepository:
    """Inject a policy-version change after approval resolution."""

    def __init__(self, repository, connection, source_id: str) -> None:
        self.repository = repository
        self.connection = connection
        self.source_id = source_id

    def _change_policy_version(self) -> None:
        self.connection.execute(
            "UPDATE data_sources SET policy_version = policy_version + 1 WHERE id = ?",
            (self.source_id,),
        )

    def resolve_once(self, *args, **kwargs):
        resolution = self.repository.resolve_once(*args, **kwargs)
        self._change_policy_version()
        self.connection.commit()
        return resolution

    def resolve_once_in_transaction(self, *args, **kwargs):
        resolution = self.repository.resolve_once_in_transaction(*args, **kwargs)
        self._change_policy_version()
        return resolution

    def __getattr__(self, name: str):
        return getattr(self.repository, name)


def _budget_attempts(connection, task_id: str) -> int:
    row = connection.execute(
        "SELECT attempts FROM task_query_budget WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    return 0 if row is None else row[0]


def test_policy_change_during_approval_resolution_rolls_back_consumption(
    tmp_path,
    fake_driver,
) -> None:
    state = _build_real_gateway(tmp_path, fake_driver)
    sql = "SELECT customer_email FROM orders"
    first = state.gateway.execute(
        state.task.id,
        state.project.id,
        state.source.id,
        sql,
    )
    state.approvals.decide(first.approval_id, approved=True)
    state.gateway.approvals = PolicyChangingApprovalRepository(
        state.approvals,
        state.connection,
        state.source.id,
    )

    changed = state.gateway.execute(
        state.task.id,
        state.project.id,
        state.source.id,
        sql,
    )

    assert changed.status == "denied"
    assert changed.error_category == "policy_changed"
    assert state.approvals.get(first.approval_id).status == "approved"
    assert fake_driver.queried_sql == []
    state.connection.close()


def test_query_timeout_rolls_back_budget_and_approved_grant(
    tmp_path,
    fake_driver,
) -> None:
    state = _build_real_gateway(tmp_path, fake_driver)
    sql = "SELECT customer_email FROM orders"
    first = state.gateway.execute(
        state.task.id,
        state.project.id,
        state.source.id,
        sql,
    )
    state.approvals.decide(first.approval_id, approved=True)
    before_attempts = _budget_attempts(state.connection, state.task.id)
    fake_driver.query_error = TimeoutError("database timeout")

    outcome = state.gateway.execute(
        state.task.id,
        state.project.id,
        state.source.id,
        sql,
    )

    assert outcome.status == "retryable_error"
    assert outcome.error_category == "query_timeout"
    assert _budget_attempts(state.connection, state.task.id) == before_attempts
    assert state.approvals.get(first.approval_id).status == "approved"
    state.connection.close()


def test_cross_connection_policy_replacement_waits_for_query_fence(
    tmp_path,
    fake_driver,
) -> None:
    state = _build_real_gateway(tmp_path, fake_driver)
    replacement_connection = sqlite3.connect(
        state.database_path,
        check_same_thread=False,
        timeout=5,
    )
    replacement_sources = DataSourceRepository(
        replacement_connection,
        PassthroughProtector(),
    )
    replacement_attempted = threading.Event()
    replacement_done = threading.Event()
    replacement_errors = []
    replacement_threads = []
    blocked_during_query = []

    def replace_policy() -> None:
        replacement_attempted.set()
        try:
            replacement_sources.replace_policies(
                state.source.id,
                {("orders", None): AccessLevel.DENIED},
            )
        except BaseException as exc:
            replacement_errors.append(exc)
        finally:
            replacement_done.set()

    def on_query() -> None:
        thread = threading.Thread(target=replace_policy)
        replacement_threads.append(thread)
        thread.start()
        assert replacement_attempted.wait(timeout=2)
        blocked_during_query.append(not replacement_done.wait(timeout=0.2))

    fake_driver.on_query = on_query

    outcome = state.gateway.execute(
        state.task.id,
        state.project.id,
        state.source.id,
        "SELECT region FROM orders",
    )

    assert outcome.status == "succeeded"
    assert blocked_during_query == [True]
    assert replacement_done.wait(timeout=5)
    for thread in replacement_threads:
        thread.join(timeout=5)
    assert replacement_errors == []
    assert replacement_sources.get_public(state.source.id).policy_version == 3
    replacement_connection.close()
    state.connection.close()


@pytest.mark.parametrize(
    "operation_name",
    ("source_create", "policy_replace", "task_transition", "artifact_register"),
)
def test_shared_connection_metadata_writes_wait_for_query_fence(
    tmp_path,
    fake_driver,
    operation_name,
) -> None:
    state = _build_real_gateway(tmp_path, fake_driver)
    secondary_task = state.tasks.create(
        state.project.id,
        f"Concurrent {operation_name}",
    )
    concurrent_artifact_path = state.workspace.resolve_inside(
        state.project.id,
        f"derived/{operation_name}.txt",
    )
    concurrent_artifact_path.parent.mkdir(parents=True, exist_ok=True)
    concurrent_artifact_path.write_text("concurrent metadata", encoding="utf-8")
    operation_attempted = threading.Event()
    operation_done = threading.Event()
    operation_errors = []
    operation_threads = []
    blocked_during_query = []

    def write_metadata() -> None:
        operation_attempted.set()
        try:
            if operation_name == "source_create":
                state.sources.create(
                    state.project.id,
                    "Concurrent source",
                    "postgresql://localhost/concurrent",
                )
            elif operation_name == "policy_replace":
                state.sources.replace_policies(
                    state.source.id,
                    {("orders", None): AccessLevel.DENIED},
                )
            elif operation_name == "task_transition":
                state.tasks.transition(secondary_task.id, TaskStatus.PLANNING)
            else:
                state.artifacts.register(
                    project_id=state.project.id,
                    task_id=secondary_task.id,
                    artifact_type=ArtifactType.DERIVED_DATASET,
                    path=concurrent_artifact_path,
                    title="Concurrent artifact",
                    description="Concurrent metadata registration",
                    producer_execution_id=None,
                    input_artifact_ids=(),
                )
        except BaseException as exc:
            operation_errors.append(exc)
        finally:
            operation_done.set()

    def on_query() -> None:
        thread = threading.Thread(target=write_metadata)
        operation_threads.append(thread)
        thread.start()
        assert operation_attempted.wait(timeout=2)
        blocked_during_query.append(not operation_done.wait(timeout=0.2))

    fake_driver.on_query = on_query

    outcome = state.gateway.execute(
        state.task.id,
        state.project.id,
        state.source.id,
        "SELECT region FROM orders",
    )

    assert outcome.status == "succeeded"
    assert blocked_during_query == [True]
    assert operation_done.wait(timeout=5)
    for thread in operation_threads:
        thread.join(timeout=5)
    assert operation_errors == []
    state.connection.close()


def test_concurrent_gateway_calls_cannot_exceed_shared_connection_budget(
    tmp_path,
    fake_driver,
) -> None:
    database_path = tmp_path / "concurrent-budget.sqlite3"
    connection = sqlite3.connect(database_path, check_same_thread=False, timeout=5)
    migrate(connection)
    settings = Settings(
        data_dir=tmp_path / "app-data",
        database_path=database_path,
        postgres_max_queries_per_task=1,
    )
    workspace = WorkspaceService(settings)
    project = ProjectRepository(connection).create("Concurrent budget")
    workspace.create_project(project.id)
    tasks = TaskRepository(connection)
    task = tasks.create(project.id, "Run one PostgreSQL query")
    source = DataSource(
        id="source-concurrent",
        project_id=project.id,
        name="Warehouse",
        dialect="postgres",
        policy_version=1,
        created_at="now",
        updated_at="now",
    )
    sources = StaticSourceRepository(
        source,
        PolicySnapshot(
            data_source_id=source.id,
            version=1,
            rules={("orders", None): AccessLevel.ALLOWED},
        ),
    )
    approvals = ApprovalRepository(connection)
    gateway = SqlSafetyGateway(
        connection=connection,
        settings=settings,
        sources=sources,
        validator=SqlAstValidator(),
        approvals=approvals,
        artifacts=ArtifactRepository(connection, workspace),
        workspace=workspace,
        tasks=tasks,
        driver=fake_driver,
    )
    barrier = threading.Barrier(2)
    outcomes = []
    errors = []

    def execute(sql: str) -> None:
        barrier.wait()
        try:
            outcomes.append(gateway.execute(task.id, project.id, source.id, sql))
        except BaseException as exc:
            errors.append(exc)

    threads = (
        threading.Thread(target=execute, args=("SELECT region FROM orders",)),
        threading.Thread(target=execute, args=("SELECT amount FROM orders",)),
    )
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert errors == []
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(outcome.status for outcome in outcomes) == ["denied", "succeeded"]
    denied = next(outcome for outcome in outcomes if outcome.status == "denied")
    assert denied.error_category == "query_budget_exhausted"
    assert connection.execute(
        "SELECT attempts FROM task_query_budget WHERE task_id = ?", (task.id,)
    ).fetchone()[0] == 1
    connection.close()


def test_concurrent_restricted_queries_share_one_pending_approval(
    tmp_path,
    fake_driver,
) -> None:
    (
        connection,
        settings,
        workspace,
        tasks,
        task,
        source,
        sources,
        approvals,
    ) = _build_threaded_gateway(
        tmp_path,
        fake_driver,
        {
            ("orders", None): AccessLevel.ALLOWED,
            ("orders", "customer_email"): AccessLevel.APPROVAL_REQUIRED,
        },
    )
    connection.close()
    approval_barrier = threading.Barrier(2)
    connections = tuple(
        sqlite3.connect(settings.database_path, check_same_thread=False, timeout=5)
        for _ in range(2)
    )
    gateways = tuple(
        SqlSafetyGateway(
            connection=thread_connection,
            settings=settings,
            sources=sources,
            validator=SqlAstValidator(),
            approvals=CoordinatedApprovalRepository(
                ApprovalRepository(thread_connection), approval_barrier
            ),
            artifacts=ArtifactRepository(thread_connection, workspace),
            workspace=workspace,
            tasks=TaskRepository(thread_connection),
            driver=fake_driver,
        )
        for thread_connection in connections
    )
    start = threading.Barrier(2)
    outcomes = []
    errors = []

    def execute(gateway: SqlSafetyGateway) -> None:
        start.wait()
        try:
            outcomes.append(
                gateway.execute(
                    task.id,
                    task.project_id,
                    source.id,
                    "SELECT customer_email FROM orders",
                )
            )
        except BaseException as exc:
            errors.append(exc)

    threads = tuple(
        threading.Thread(target=execute, args=(gateway,)) for gateway in gateways
    )
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert errors == []
    assert all(not thread.is_alive() for thread in threads)
    assert {outcome.status for outcome in outcomes} == {"approval_required"}
    assert len({outcome.approval_id for outcome in outcomes}) == 1
    assert len(ApprovalRepository(connections[0]).list_for_task(task.id)) == 1
    for thread_connection in connections:
        thread_connection.close()


def test_existing_approved_action_is_not_shadowed_by_newer_pending_record(
    sql_gateway,
    source_repository,
    approval_repository,
    task,
) -> None:
    source = _create_source(source_repository, task.project_id)
    sql = "SELECT customer_email FROM orders"
    first = sql_gateway.execute(task.id, task.project_id, source.id, sql)
    approval_repository.decide(first.approval_id, approved=True)
    approved_action = approval_repository.get(first.approval_id).action
    shadow = approval_repository.request(
        task.id,
        approved_action,
        "Concurrent duplicate pending approval",
    )

    outcome = sql_gateway.execute(task.id, task.project_id, source.id, sql)

    assert outcome.status == "succeeded"
    assert approval_repository.get(first.approval_id).status == "consumed"
    assert approval_repository.get(shadow.id).status == "pending"


def test_concurrent_registration_failure_never_deletes_successful_artifact(
    tmp_path,
    fake_driver,
) -> None:
    (
        connection,
        settings,
        workspace,
        tasks,
        task,
        source,
        sources,
        approvals,
    ) = _build_threaded_gateway(
        tmp_path,
        fake_driver,
        {("orders", None): AccessLevel.ALLOWED},
    )
    artifacts = ArtifactRepository(connection, workspace)
    gateway = CoordinatedPathGateway(
        connection=connection,
        settings=settings,
        sources=sources,
        validator=SqlAstValidator(),
        approvals=approvals,
        artifacts=FailSecondRegistrationRepository(artifacts),
        workspace=workspace,
        tasks=tasks,
        driver=fake_driver,
        path_barrier=threading.Barrier(2),
    )
    start = threading.Barrier(2)
    outcomes = []
    errors = []

    def execute() -> None:
        start.wait()
        try:
            outcomes.append(
                gateway.execute(
                    task.id,
                    task.project_id,
                    source.id,
                    "SELECT region FROM orders",
                )
            )
        except BaseException as exc:
            errors.append(exc)

    threads = (threading.Thread(target=execute), threading.Thread(target=execute))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert len(outcomes) == 1
    assert outcomes[0].status == "succeeded"
    assert len(errors) == 1
    assert all(not thread.is_alive() for thread in threads)
    registered = artifacts.list_for_task(task.id)
    assert len(registered) == 1
    assert registered[0].path.is_file()
    assert registered[0].path.read_text(encoding="utf-8") == (
        "region,amount\nEast,10\n"
    )
    connection.close()


@pytest.mark.parametrize(
    ("plan", "expected"),
    [
        (
            {"Plan": {"Total Cost": 42, "Plan Rows": 12, "Node Type": "Index Scan"}},
            "low",
        ),
        (
            {
                "Plan": {
                    "Total Cost": 1_000,
                    "Plan Rows": 200_000,
                    "Node Type": "Nested Loop",
                    "Plans": [
                        {"Node Type": "Seq Scan", "Plan Rows": 100_000},
                        {"Node Type": "Seq Scan", "Plan Rows": 100_000},
                    ],
                }
            },
            "medium",
        ),
        (
            {
                "Plan": {
                    "Total Cost": 100_000,
                    "Plan Rows": 12,
                    "Node Type": "Index Scan",
                }
            },
            "high",
        ),
        (
            {
                "Plan": {
                    "Total Cost": 1_000_000,
                    "Plan Rows": 12,
                    "Node Type": "Index Scan",
                }
            },
            "extreme",
        ),
    ],
)
def test_explain_risk_uses_cost_rows_scans_and_joins(settings, plan, expected) -> None:
    risk = ExplainRiskPolicy(settings).classify(plan)

    assert risk.level == expected
    assert risk.total_cost == float(plan["Plan"]["Total Cost"])
    if expected == "medium":
        assert risk.sequential_scans == 2
        assert risk.join_count == 1


def test_high_risk_requires_approval_while_extreme_risk_is_denied(
    sql_gateway,
    source_repository,
    approval_repository,
    fake_driver,
    task,
) -> None:
    source = _create_source(source_repository, task.project_id)
    fake_driver.plan = {
        "Plan": {"Total Cost": 100_000, "Plan Rows": 12, "Node Type": "Index Scan"}
    }

    high = sql_gateway.execute(
        task.id, task.project_id, source.id, "SELECT region FROM orders"
    )

    assert high.status == "approval_required"
    assert high.risk.level == "high"
    assert len(approval_repository.list_for_task(task.id)) == 1

    fake_driver.plan = {
        "Plan": {
            "Total Cost": 1_000_000,
            "Plan Rows": 12,
            "Node Type": "Index Scan",
        }
    }
    extreme = sql_gateway.execute(
        task.id, task.project_id, source.id, "SELECT amount FROM orders"
    )

    assert extreme.status == "denied"
    assert extreme.error_category == "extreme_query_risk"
    assert extreme.risk.level == "extreme"
    assert len(approval_repository.list_for_task(task.id)) == 1


class SemanticPostgresConnection:
    def __init__(self) -> None:
        self.read_only = False
        self.local_settings: dict[str, str] = {}
        self.explain_target: str | None = None
        self.query_target: str | None = None
        self.query_limit: int | None = None
        self.description = (
            SimpleNamespace(name="region"),
            SimpleNamespace(name="amount"),
        )
        self.rows = (("East", 10),)
        self._one = None

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def transaction(self):
        return nullcontext()

    def cursor(self):
        return self

    def execute(self, sql: str, parameters=()):
        if sql == "SET TRANSACTION READ ONLY":
            self.read_only = True
        elif sql.startswith("SELECT set_config("):
            setting = sql.split("'", 2)[1]
            assert "%s" in sql
            assert parameters[0] not in sql
            self.local_settings[setting] = parameters[0]
        elif sql.startswith("EXPLAIN (FORMAT JSON) "):
            self.explain_target = sql.removeprefix("EXPLAIN (FORMAT JSON) ")
            self._one = ([{"Plan": {"Total Cost": 5, "Plan Rows": 1}}],)
        elif sql.startswith("SELECT * FROM ("):
            self.query_target = sql
            self.query_limit = parameters[0]
        elif "information_schema.columns" in sql:
            self.rows = (
                ("orders", "region"),
                ("orders", "amount"),
                ("customers", "id"),
            )
        else:
            raise AssertionError(f"Unexpected SQL: {sql}")
        return self

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self.rows


class SemanticConnector:
    def __init__(self) -> None:
        self.connections: list[SemanticPostgresConnection] = []

    def __call__(self, dsn: str) -> SemanticPostgresConnection:
        connection = SemanticPostgresConnection()
        self.connections.append(connection)
        return connection


def test_psycopg_driver_uses_read_only_parameterized_timeouts_and_safe_wrappers() -> None:
    connect = SemanticConnector()
    driver = PsycopgDriver(connect=connect, lock_timeout_ms=1_234)

    plan = driver.explain(
        "postgresql://localhost/db",
        "SELECT region FROM orders",
        statement_timeout_ms=8_765,
    )
    columns, rows = driver.query(
        "postgresql://localhost/db",
        "SELECT region, amount FROM orders",
        statement_timeout_ms=4_321,
        row_limit=99,
    )

    explain_connection, query_connection = connect.connections
    assert explain_connection.read_only is True
    assert explain_connection.local_settings == {
        "statement_timeout": "8765",
        "lock_timeout": "1234",
    }
    assert explain_connection.explain_target == "SELECT region FROM orders"
    assert plan == {"Plan": {"Total Cost": 5, "Plan Rows": 1}}
    assert query_connection.read_only is True
    assert query_connection.local_settings == {
        "statement_timeout": "4321",
        "lock_timeout": "1234",
    }
    assert query_connection.query_target == (
        "SELECT * FROM (SELECT region, amount FROM orders) AS safe_query LIMIT %s"
    )
    assert query_connection.query_limit == 99
    assert columns == ("region", "amount")
    assert rows == (("East", 10),)
    assert "ANALYZE" not in explain_connection.explain_target


def test_psycopg_driver_catalog_contains_unqualified_names_only() -> None:
    connect = SemanticConnector()
    driver = PsycopgDriver(connect=connect)

    catalog = driver.inspect_schema("postgresql://localhost/db")

    assert catalog == {
        "orders": ("region", "amount"),
        "customers": ("id",),
    }
    assert all("." not in table for table in catalog)
