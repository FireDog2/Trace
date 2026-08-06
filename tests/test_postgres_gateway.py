from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace

import pytest

from data_analysis_agent.data_sources import AccessLevel
from data_analysis_agent.domain import ArtifactType, TaskStatus
from data_analysis_agent.permissions import ToolAction
from data_analysis_agent.postgres import (
    ExplainRiskPolicy,
    PsycopgDriver,
    SqlSafetyGateway,
)
from data_analysis_agent.sql_safety import SqlAstValidator


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
