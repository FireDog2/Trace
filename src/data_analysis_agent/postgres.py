from __future__ import annotations

import csv
import io
import re
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import psycopg

from data_analysis_agent.data_sources import (
    AccessLevel,
    DataSourceRepository,
)
from data_analysis_agent.db import sqlite_connection_lock, utc_now
from data_analysis_agent.domain import ArtifactType, TaskStatus
from data_analysis_agent.permissions import ApprovalRepository, ToolAction
from data_analysis_agent.repositories import (
    ArtifactRepository,
    RecordNotFoundError,
    TaskRepository,
)
from data_analysis_agent.settings import Settings
from data_analysis_agent.sql_safety import (
    SchemaCatalog,
    SqlAstValidator,
    SqlValidationError,
    ValidatedSql,
)
from data_analysis_agent.workspace import WorkspaceService


class PostgresDriver(Protocol):
    def inspect_schema(self, dsn: str) -> SchemaCatalog: ...

    def explain(
        self,
        dsn: str,
        sql: str,
        *,
        statement_timeout_ms: int,
    ) -> Mapping[str, Any]: ...

    def query(
        self,
        dsn: str,
        sql: str,
        *,
        statement_timeout_ms: int,
        row_limit: int,
    ) -> tuple[Sequence[str], Sequence[Sequence[Any]]]: ...


@dataclass(frozen=True, slots=True)
class ExplainRisk:
    level: str
    total_cost: float
    estimated_rows: int
    sequential_scans: int
    join_count: int
    reason: str


@dataclass(frozen=True, slots=True)
class SqlGatewayOutcome:
    status: str
    query_hash: str | None = None
    risk: ExplainRisk | None = None
    approval_id: str | None = None
    artifact_id: str | None = None
    columns: tuple[str, ...] = ()
    preview: tuple[dict[str, Any], ...] = ()
    row_count: int = 0
    error_category: str | None = None
    message: str | None = None
    retryable: bool = False


class ExplainRiskPolicy:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def classify(self, plan: Mapping[str, Any] | Sequence[Any]) -> ExplainRisk:
        root = _plan_root(plan)
        nodes = tuple(_plan_nodes(root))
        total_cost = float(root.get("Total Cost", 0.0) or 0.0)
        estimated_rows = int(root.get("Plan Rows", 0) or 0)
        sequential_scans = sum(
            str(node.get("Node Type", "")).lower() == "seq scan"
            for node in nodes
        )
        join_count = sum(_is_join_node(node) for node in nodes)

        if (
            total_cost >= self._settings.postgres_extreme_cost
            or estimated_rows >= 10_000_000
            or sequential_scans >= 8
            or join_count >= 10
        ):
            level = "extreme"
            reason = "The query plan exceeds the automatic safety ceiling"
        elif (
            total_cost >= self._settings.postgres_high_cost
            or estimated_rows >= 1_000_000
            or sequential_scans >= 4
            or join_count >= 6
        ):
            level = "high"
            reason = "The query plan requires one-time approval"
        elif (
            total_cost >= self._settings.postgres_high_cost / 10
            or estimated_rows >= 100_000
            or sequential_scans >= 1
            or join_count >= 3
        ):
            level = "medium"
            reason = "The query plan has bounded elevated cost"
        else:
            level = "low"
            reason = "The query plan is within automatic limits"

        return ExplainRisk(
            level=level,
            total_cost=total_cost,
            estimated_rows=estimated_rows,
            sequential_scans=sequential_scans,
            join_count=join_count,
            reason=reason,
        )


class PsycopgDriver:
    def __init__(
        self,
        *,
        connect: Callable[[str], Any] = psycopg.connect,
        statement_timeout_ms: int = 8_000,
        lock_timeout_ms: int = 1_000,
    ) -> None:
        self._connect = connect
        self._statement_timeout_ms = statement_timeout_ms
        self._lock_timeout_ms = lock_timeout_ms

    def inspect_schema(self, dsn: str) -> SchemaCatalog:
        with self._connect(dsn) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    _configure_read_only(
                        cursor,
                        statement_timeout_ms=self._statement_timeout_ms,
                        lock_timeout_ms=self._lock_timeout_ms,
                    )
                    cursor.execute(
                        "SELECT table_name, column_name "
                        "FROM information_schema.columns "
                        "WHERE table_schema = ANY (current_schemas(false)) "
                        "ORDER BY table_name, ordinal_position"
                    )
                    rows = cursor.fetchall()
        catalog: dict[str, list[str]] = {}
        for table, column in rows:
            catalog.setdefault(_catalog_identifier(str(table)), []).append(
                _catalog_identifier(str(column))
            )
        return {table: tuple(columns) for table, columns in catalog.items()}

    def explain(
        self,
        dsn: str,
        sql: str,
        *,
        statement_timeout_ms: int,
    ) -> Mapping[str, Any]:
        with self._connect(dsn) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    _configure_read_only(
                        cursor,
                        statement_timeout_ms=statement_timeout_ms,
                        lock_timeout_ms=self._lock_timeout_ms,
                    )
                    cursor.execute(f"EXPLAIN (FORMAT JSON) {sql}")
                    row = cursor.fetchone()
        if row is None:
            raise ValueError("PostgreSQL returned no EXPLAIN plan")
        plan = row[0]
        root = _plan_root(plan)
        return {"Plan": root}

    def query(
        self,
        dsn: str,
        sql: str,
        *,
        statement_timeout_ms: int,
        row_limit: int,
    ) -> tuple[tuple[str, ...], tuple[tuple[Any, ...], ...]]:
        with self._connect(dsn) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    _configure_read_only(
                        cursor,
                        statement_timeout_ms=statement_timeout_ms,
                        lock_timeout_ms=self._lock_timeout_ms,
                    )
                    cursor.execute(
                        f"SELECT * FROM ({sql}) AS safe_query LIMIT %s",
                        (row_limit,),
                    )
                    description = cursor.description or ()
                    columns = tuple(column.name for column in description)
                    rows = tuple(tuple(row) for row in cursor.fetchall())
        return columns, rows


class SqlSafetyGateway:
    def __init__(
        self,
        *,
        connection: sqlite3.Connection,
        settings: Settings,
        sources: DataSourceRepository,
        validator: SqlAstValidator,
        approvals: ApprovalRepository,
        artifacts: ArtifactRepository,
        workspace: WorkspaceService,
        tasks: TaskRepository,
        driver: PostgresDriver | None = None,
    ) -> None:
        self._connection = connection
        self._metadata_lock = sqlite_connection_lock(connection)
        self.settings = settings
        self.sources = sources
        self.validator = validator
        self.approvals = approvals
        self.artifacts = artifacts
        self.workspace = workspace
        self.tasks = tasks
        self.driver = driver or PsycopgDriver(
            statement_timeout_ms=settings.postgres_statement_timeout_ms,
            lock_timeout_ms=settings.postgres_lock_timeout_ms,
        )

    def execute(
        self,
        task_id: str,
        project_id: str,
        data_source_id: str,
        sql: str,
    ) -> SqlGatewayOutcome:
        try:
            task = self.tasks.get(task_id)
            source = self.sources.get_public(data_source_id)
        except RecordNotFoundError:
            return _denied("resource_not_found", "Task or data source was not found")
        if task.project_id != project_id or source.project_id != project_id:
            return _denied(
                "project_scope_denied",
                "Task and data source must belong to the requested project",
            )
        if source.dialect != "postgres":
            return _denied(
                "unsupported_database",
                "Only PostgreSQL data sources are supported",
            )

        policy = self.sources.policy_snapshot(data_source_id)
        credentials = self.sources.credentials(data_source_id)
        try:
            catalog = self.driver.inspect_schema(credentials.dsn)
            validated = self.validator.validate(sql, catalog, policy)
        except SqlValidationError as exc:
            return _denied(exc.code, exc.message)
        except BaseException as exc:
            return _driver_error(exc)

        if validated.access_level is AccessLevel.DENIED:
            return _denied(
                "policy_denied",
                "The query references data denied by the active policy",
                query_hash=validated.sql_hash,
            )

        try:
            plan = self.driver.explain(
                credentials.dsn,
                validated.normalized_sql,
                statement_timeout_ms=self.settings.postgres_statement_timeout_ms,
            )
        except BaseException as exc:
            return _driver_error(exc, query_hash=validated.sql_hash)
        risk = ExplainRiskPolicy(self.settings).classify(plan)
        if not self._policy_version_matches(data_source_id, policy.version):
            return _policy_changed(validated.sql_hash, risk)
        if risk.level == "extreme":
            return _denied(
                "extreme_query_risk",
                "The query plan exceeds the automatic safety ceiling",
                query_hash=validated.sql_hash,
                risk=risk,
            )

        approval_reason = _approval_reason(validated, risk)
        pending_resolution = None
        path = None
        with self._metadata_lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                if not self._policy_version_matches_in_transaction(
                    data_source_id,
                    policy.version,
                ):
                    self._connection.rollback()
                    return _policy_changed(validated.sql_hash, risk)

                if approval_reason is not None:
                    action = _approval_action(
                        data_source_id,
                        validated,
                        policy.version,
                    )
                    resolution = self.approvals.resolve_once_in_transaction(
                        task_id,
                        action,
                        approval_reason,
                    )
                    if not self._policy_version_matches_in_transaction(
                        data_source_id,
                        policy.version,
                    ):
                        self._connection.rollback()
                        return _policy_changed(validated.sql_hash, risk)
                    if resolution.status == "rejected":
                        self._connection.commit()
                        return _denied(
                            "approval_rejected",
                            "The exact PostgreSQL query was rejected",
                            query_hash=validated.sql_hash,
                            risk=risk,
                        )
                    if resolution.status == "pending":
                        pending_resolution = resolution
                        if resolution.created:
                            current_task = self.tasks.get(task_id)
                            if current_task.status is not TaskStatus.NEEDS_INPUT:
                                self.tasks.transition_in_transaction(
                                    task_id,
                                    TaskStatus.NEEDS_INPUT,
                                )
                            self.tasks.append_event_in_transaction(
                                task_id,
                                "approval.requested",
                                {
                                    "approval_id": resolution.approval.id,
                                    "sql_hash": validated.sql_hash,
                                    "policy_version": policy.version,
                                    "reason": approval_reason,
                                },
                            )
                        self._connection.commit()

                if pending_resolution is None:
                    if not self._consume_budget_in_transaction(task_id):
                        self._connection.rollback()
                        return _denied(
                            "query_budget_exhausted",
                            "The per-task PostgreSQL query budget is exhausted",
                            query_hash=validated.sql_hash,
                            risk=risk,
                        )
                    try:
                        raw_columns, raw_rows = self.driver.query(
                            credentials.dsn,
                            validated.normalized_sql,
                            statement_timeout_ms=(
                                self.settings.postgres_statement_timeout_ms
                            ),
                            row_limit=self.settings.postgres_max_rows,
                        )
                    except BaseException as exc:
                        self._connection.rollback()
                        return _driver_error(
                            exc,
                            query_hash=validated.sql_hash,
                            risk=risk,
                        )
                    columns = tuple(str(column) for column in raw_columns)
                    rows = tuple(tuple(row) for row in raw_rows)
                    if len(rows) > self.settings.postgres_max_rows:
                        self._connection.rollback()
                        return _result_limit(validated.sql_hash, risk)
                    try:
                        serialized = _csv_bytes(columns, rows)
                    except (csv.Error, UnicodeError, TypeError, ValueError):
                        self._connection.rollback()
                        return SqlGatewayOutcome(
                            status="retryable_error",
                            query_hash=validated.sql_hash,
                            risk=risk,
                            error_category="result_serialization_error",
                            message=(
                                "The PostgreSQL result could not be serialized safely"
                            ),
                            retryable=True,
                        )
                    if len(serialized) > self.settings.postgres_max_result_bytes:
                        self._connection.rollback()
                        return _result_limit(validated.sql_hash, risk)

                    try:
                        path = self._query_artifact_path(
                            project_id,
                            validated.sql_hash,
                        )
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(serialized)
                    except OSError:
                        self._connection.rollback()
                        if path is not None:
                            with suppress(OSError):
                                path.unlink(missing_ok=True)
                        return SqlGatewayOutcome(
                            status="retryable_error",
                            query_hash=validated.sql_hash,
                            risk=risk,
                            error_category="artifact_write_error",
                            message=(
                                "The PostgreSQL result artifact could not be "
                                "written safely"
                            ),
                            retryable=True,
                        )

                    try:
                        artifact = self.artifacts.register_in_transaction(
                            project_id=project_id,
                            task_id=task_id,
                            artifact_type=ArtifactType.DERIVED_DATASET,
                            path=path,
                            title="PostgreSQL query result",
                            description=(
                                "Bounded PostgreSQL result for query "
                                f"{validated.sql_hash[:12]} "
                                f"({risk.level} explain risk)"
                            ),
                            producer_execution_id=None,
                            input_artifact_ids=(),
                        )
                    except Exception:
                        self._connection.rollback()
                        with suppress(OSError):
                            path.unlink(missing_ok=True)
                        return SqlGatewayOutcome(
                            status="retryable_error",
                            query_hash=validated.sql_hash,
                            risk=risk,
                            error_category="artifact_registration_error",
                            message=(
                                "The PostgreSQL result artifact could not be "
                                "registered safely"
                            ),
                            retryable=True,
                        )

                    preview = tuple(
                        dict(zip(columns, row, strict=True))
                        for row in rows[: min(len(rows), 20)]
                    )
                    outcome = SqlGatewayOutcome(
                        status="succeeded",
                        query_hash=validated.sql_hash,
                        risk=risk,
                        artifact_id=artifact.id,
                        columns=columns,
                        preview=preview,
                        row_count=len(rows),
                    )
                    self._connection.commit()
                    return outcome
            except BaseException:
                self._connection.rollback()
                if path is not None:
                    with suppress(OSError):
                        path.unlink(missing_ok=True)
                raise

        if pending_resolution is not None:
            approval = pending_resolution.approval
            return SqlGatewayOutcome(
                status="approval_required",
                query_hash=validated.sql_hash,
                risk=risk,
                approval_id=approval.id,
                message=approval_reason,
            )

    def _consume_budget(self, task_id: str) -> bool:
        with self._metadata_lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                consumed = self._consume_budget_in_transaction(task_id)
                if not consumed:
                    self._connection.rollback()
                    return False
                self._connection.commit()
                return True
            except Exception:
                self._connection.rollback()
                raise

    def _consume_budget_in_transaction(self, task_id: str) -> bool:
        row = self._connection.execute(
            "SELECT attempts FROM task_query_budget WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        attempts = int(row["attempts"] if row is not None else 0)
        if attempts >= self.settings.postgres_max_queries_per_task:
            return False
        self._connection.execute(
            "INSERT INTO task_query_budget(task_id, attempts, updated_at) "
            "VALUES (?, 1, ?) ON CONFLICT(task_id) DO UPDATE SET "
            "attempts = attempts + 1, updated_at = excluded.updated_at",
            (task_id, utc_now()),
        )
        return True

    def _policy_version_matches_in_transaction(
        self,
        data_source_id: str,
        expected_version: int,
    ) -> bool:
        row = self._connection.execute(
            "SELECT policy_version FROM data_sources WHERE id = ?",
            (data_source_id,),
        ).fetchone()
        if row is not None:
            return int(row["policy_version"]) == expected_version
        return self._policy_version_matches(data_source_id, expected_version)

    def _policy_version_matches(
        self,
        data_source_id: str,
        expected_version: int,
    ) -> bool:
        try:
            return self.sources.get_public(data_source_id).policy_version == expected_version
        except RecordNotFoundError:
            return False

    def _query_artifact_path(self, project_id: str, sql_hash: str) -> Path:
        base = self.workspace.resolve_inside(
            project_id,
            f"derived/postgres-query-{sql_hash[:12]}.csv",
        )
        base.parent.mkdir(parents=True, exist_ok=True)
        candidate = base
        suffix = 1
        while True:
            try:
                candidate.touch(exist_ok=False)
                return candidate
            except FileExistsError:
                pass
            suffix += 1
            candidate = base.with_name(f"{base.stem}-{suffix}{base.suffix}")


def _configure_read_only(
    cursor: Any,
    *,
    statement_timeout_ms: int,
    lock_timeout_ms: int,
) -> None:
    cursor.execute("SET TRANSACTION READ ONLY")
    cursor.execute(
        "SELECT set_config('statement_timeout', %s, true)",
        (str(statement_timeout_ms),),
    )
    cursor.execute(
        "SELECT set_config('lock_timeout', %s, true)",
        (str(lock_timeout_ms),),
    )


def _plan_root(plan: Mapping[str, Any] | Sequence[Any]) -> Mapping[str, Any]:
    candidate: Any = plan
    if isinstance(candidate, Sequence) and not isinstance(candidate, str | bytes):
        if not candidate:
            raise ValueError("PostgreSQL returned an empty EXPLAIN plan")
        candidate = candidate[0]
    if not isinstance(candidate, Mapping):
        raise ValueError("PostgreSQL returned an invalid EXPLAIN plan")
    root = candidate.get("Plan", candidate)
    if not isinstance(root, Mapping):
        raise ValueError("PostgreSQL returned an invalid EXPLAIN plan")
    return root


def _plan_nodes(root: Mapping[str, Any]):
    yield root
    children = root.get("Plans", ())
    if not isinstance(children, Sequence) or isinstance(children, str | bytes):
        return
    for child in children:
        if isinstance(child, Mapping):
            yield from _plan_nodes(child)


def _is_join_node(node: Mapping[str, Any]) -> bool:
    node_type = str(node.get("Node Type", "")).lower()
    return "join" in node_type or node_type == "nested loop"


def _approval_action(
    data_source_id: str,
    validated: ValidatedSql,
    policy_version: int,
) -> ToolAction:
    return ToolAction(
        "query_database",
        {
            "data_source_id": data_source_id,
            "sql_hash": validated.sql_hash,
            "policy_version": policy_version,
        },
    )


def _approval_reason(validated: ValidatedSql, risk: ExplainRisk) -> str | None:
    if validated.access_level is AccessLevel.APPROVAL_REQUIRED:
        return "Restricted fields require one-time approval"
    if risk.level == "high":
        return "High-risk query plan requires one-time approval"
    return None


def _csv_bytes(
    columns: tuple[str, ...],
    rows: tuple[tuple[Any, ...], ...],
) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        if len(row) != len(columns):
            raise ValueError("PostgreSQL result row does not match its columns")
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8")


def _result_limit(sql_hash: str, risk: ExplainRisk) -> SqlGatewayOutcome:
    return SqlGatewayOutcome(
        status="retryable_error",
        query_hash=sql_hash,
        risk=risk,
        error_category="result_limit",
        message="The PostgreSQL result exceeded the configured output limit",
        retryable=True,
    )


def _policy_changed(sql_hash: str, risk: ExplainRisk) -> SqlGatewayOutcome:
    return _denied(
        "policy_changed",
        "The data-access policy changed while the query was being checked",
        query_hash=sql_hash,
        risk=risk,
    )


def _driver_error(
    error: BaseException,
    *,
    query_hash: str | None = None,
    risk: ExplainRisk | None = None,
) -> SqlGatewayOutcome:
    if isinstance(error, (TimeoutError, psycopg.errors.QueryCanceled)):
        category = "query_timeout"
        message = "The PostgreSQL query exceeded its statement timeout"
        retryable = True
    elif isinstance(
        error,
        (ConnectionError, psycopg.OperationalError, psycopg.errors.ConnectionException),
    ):
        category = "connection_error"
        message = "The PostgreSQL data source could not be reached"
        retryable = True
    elif isinstance(error, ValueError):
        category = "explain_error"
        message = "PostgreSQL returned an invalid query plan"
        retryable = True
    else:
        category = "query_error"
        message = "PostgreSQL could not execute the bounded query"
        retryable = False
    return SqlGatewayOutcome(
        status="retryable_error",
        query_hash=query_hash,
        risk=risk,
        error_category=category,
        message=message,
        retryable=retryable,
    )


def _denied(
    category: str,
    message: str,
    *,
    query_hash: str | None = None,
    risk: ExplainRisk | None = None,
) -> SqlGatewayOutcome:
    return SqlGatewayOutcome(
        status="denied",
        query_hash=query_hash,
        risk=risk,
        error_category=category,
        message=message,
        retryable=False,
    )


_UNQUOTED_POSTGRES_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_$]*$")


def _catalog_identifier(value: str) -> str:
    if _UNQUOTED_POSTGRES_IDENTIFIER.fullmatch(value):
        return value
    return f'"{value.replace(chr(34), chr(34) * 2)}"'
