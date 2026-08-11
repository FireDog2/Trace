from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from urllib.parse import urlsplit
from uuid import uuid4

from data_analysis_agent.db import (
    sqlite_connection_guarded,
    sqlite_connection_lock,
    utc_now,
)
from data_analysis_agent.model_config import SecretProtector
from data_analysis_agent.repositories import RecordNotFoundError, RepositoryError


class CredentialError(RepositoryError):
    """Raised when a data-source credential cannot be accessed safely."""


class PolicyVersionConflict(RepositoryError):
    """Raised when a policy editor tries to replace a stale snapshot."""


class AccessLevel(StrEnum):
    ALLOWED = "allowed"
    APPROVAL_REQUIRED = "approval_required"
    DENIED = "denied"


@dataclass(frozen=True, slots=True)
class DataSource:
    id: str
    project_id: str
    name: str
    dialect: str
    policy_version: int
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class DataSourceCredentials:
    id: str
    dsn: str


@dataclass(frozen=True, slots=True)
class PolicySnapshot:
    data_source_id: str
    version: int
    rules: dict[tuple[str, str | None], AccessLevel]

    def __post_init__(self) -> None:
        object.__setattr__(self, "rules", MappingProxyType(dict(self.rules)))

    def resolve(self, table: str, column: str) -> AccessLevel:
        table_name = _normalize_identifier(table)
        column_name = _normalize_identifier(column)
        return self.rules.get(
            (table_name, column_name),
            self.rules.get((table_name, None), AccessLevel.DENIED),
        )


class DataSourceRepository:
    def __init__(
        self,
        connection: sqlite3.Connection,
        secret_protector: SecretProtector,
        *,
        attempt_stale_after_seconds: float = 16.0,
    ) -> None:
        connection.row_factory = sqlite3.Row
        self._connection = connection
        self._lock = sqlite_connection_lock(connection)
        self._secret_protector = secret_protector
        self._attempt_stale_after = timedelta(seconds=attempt_stale_after_seconds)

    @sqlite_connection_guarded
    def create(self, project_id: str, name: str, dsn: str) -> DataSource:
        normalized_dsn = _postgres_dsn(dsn)
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("Data source name is required")
        source_id = str(uuid4())
        now = utc_now()
        try:
            protected_dsn = self._secret_protector.protect(normalized_dsn)
        except BaseException:
            raise CredentialError("Data source credentials are unavailable") from None
        try:
            self._connection.execute(
                "INSERT INTO data_sources("
                "id, project_id, name, dialect, protected_dsn, policy_version, "
                "created_at, updated_at"
                ") VALUES (?, ?, ?, 'postgres', ?, 1, ?, ?)",
                (
                    source_id,
                    project_id,
                    normalized_name,
                    sqlite3.Binary(protected_dsn),
                    now,
                    now,
                ),
            )
            self._connection.commit()
        except sqlite3.IntegrityError as exc:
            self._connection.rollback()
            raise RepositoryError("Unable to create data source") from exc
        return self.get_public(source_id)

    @sqlite_connection_guarded
    def update(
        self,
        data_source_id: str,
        *,
        name: str,
        dsn: str | None = None,
    ) -> DataSource:
        if self._public_row(data_source_id) is None:
            raise RecordNotFoundError(f"Data source not found: {data_source_id}")
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("Data source name is required")
        parameters: tuple[object, ...]
        if dsn is not None:
            normalized_dsn = _postgres_dsn(dsn)
            try:
                protected_dsn = self._secret_protector.protect(normalized_dsn)
            except BaseException:
                raise CredentialError("Data source credentials are unavailable") from None
            sql = "UPDATE data_sources SET name = ?, protected_dsn = ?, updated_at = ? WHERE id = ?"
            parameters = (normalized_name, sqlite3.Binary(protected_dsn), utc_now(), data_source_id)
        else:
            sql = "UPDATE data_sources SET name = ?, updated_at = ? WHERE id = ?"
            parameters = (normalized_name, utc_now(), data_source_id)
        try:
            self._connection.execute(sql, parameters)
            self._connection.commit()
        except sqlite3.IntegrityError as exc:
            self._connection.rollback()
            raise RepositoryError("Unable to update data source") from exc
        return self.get_public(data_source_id)

    @sqlite_connection_guarded
    def get_public(self, data_source_id: str) -> DataSource:
        row = self._public_row(data_source_id)
        if row is None:
            raise RecordNotFoundError(f"Data source not found: {data_source_id}")
        return _data_source_from_row(row)

    @sqlite_connection_guarded
    def credentials(self, data_source_id: str) -> DataSourceCredentials:
        row = self._connection.execute(
            "SELECT id, protected_dsn FROM data_sources "
            "WHERE id = ? AND deleted_at IS NULL",
            (data_source_id,),
        ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"Data source not found: {data_source_id}")
        try:
            dsn = self._secret_protector.unprotect(bytes(row["protected_dsn"]))
        except BaseException as exc:
            raise CredentialError("Data source credentials are unavailable") from exc
        return DataSourceCredentials(id=row["id"], dsn=dsn)

    @sqlite_connection_guarded
    def list_for_project(self, project_id: str) -> tuple[DataSource, ...]:
        rows = self._connection.execute(
            "SELECT id, project_id, name, dialect, policy_version, created_at, updated_at "
            "FROM data_sources WHERE project_id = ? AND deleted_at IS NULL "
            "ORDER BY created_at, rowid",
            (project_id,),
        ).fetchall()
        return tuple(_data_source_from_row(row) for row in rows)

    @sqlite_connection_guarded
    def list_all(self) -> tuple[DataSource, ...]:
        rows = self._connection.execute(
            "SELECT id, project_id, name, dialect, policy_version, created_at, updated_at "
            "FROM data_sources WHERE deleted_at IS NULL "
            "ORDER BY name COLLATE NOCASE, created_at, rowid"
        ).fetchall()
        return tuple(_data_source_from_row(row) for row in rows)

    @sqlite_connection_guarded
    def delete(self, data_source_id: str) -> None:
        """Retire a source while preserving its historical query audit trail."""
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            if self._public_row(data_source_id) is None:
                raise RecordNotFoundError(f"Data source not found: {data_source_id}")
            self._recover_stale_attempts_in_transaction(data_source_id)
            active_attempt = self._connection.execute(
                "SELECT id FROM database_query_attempts WHERE data_source_id = ? "
                "AND status = 'in_flight' LIMIT 1",
                (data_source_id,),
            ).fetchone()
            if active_attempt is not None:
                raise RepositoryError(
                    "Cannot delete data source while a query is in flight"
                )
            now = utc_now()
            self._connection.execute(
                "UPDATE data_sources SET name = ?, protected_dsn = ?, deleted_at = ?, "
                "updated_at = ? WHERE id = ? AND deleted_at IS NULL",
                (
                    f"__deleted__:{data_source_id}",
                    sqlite3.Binary(b""),
                    now,
                    now,
                    data_source_id,
                ),
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    @sqlite_connection_guarded
    def replace_policies(
        self,
        data_source_id: str,
        rules: dict[tuple[str, str | None], AccessLevel],
        *,
        expected_version: int | None = None,
    ) -> PolicySnapshot:
        normalized_rules: dict[tuple[str, str | None], AccessLevel] = {}
        for (table, column), level in rules.items():
            key = (
                _normalize_identifier(table),
                _normalize_identifier(column) if column is not None else None,
            )
            if key in normalized_rules:
                raise ValueError(f"Duplicate data-access policy: {key}")
            normalized_rules[key] = AccessLevel(level)
        now = utc_now()
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            source_row = self._public_row(data_source_id)
            if source_row is None:
                raise RecordNotFoundError(f"Data source not found: {data_source_id}")
            if expected_version is not None and int(source_row["policy_version"]) != expected_version:
                raise PolicyVersionConflict("Data-access policy has changed")
            self._recover_stale_attempts_in_transaction(data_source_id)
            active_attempt = self._connection.execute(
                "SELECT id FROM database_query_attempts WHERE data_source_id = ? "
                "AND status = 'in_flight' LIMIT 1",
                (data_source_id,),
            ).fetchone()
            if active_attempt is not None:
                raise RepositoryError(
                    "Cannot replace data-access policy while a query is in flight"
                )
            self._connection.execute(
                "DELETE FROM data_access_policies WHERE data_source_id = ?",
                (data_source_id,),
            )
            self._connection.executemany(
                "INSERT INTO data_access_policies("
                "id, data_source_id, table_name, column_name, access_level, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    (
                        str(uuid4()),
                        data_source_id,
                        table,
                        column,
                        level.value,
                        now,
                        now,
                    )
                    for (table, column), level in normalized_rules.items()
                ),
            )
            self._connection.execute(
                "UPDATE data_sources SET policy_version = policy_version + 1, updated_at = ? "
                "WHERE id = ?",
                (now, data_source_id),
            )
            snapshot = self.policy_snapshot(data_source_id)
            self._connection.commit()
            return snapshot
        except Exception:
            self._connection.rollback()
            raise

    @sqlite_connection_guarded
    def policy_snapshot(self, data_source_id: str) -> PolicySnapshot:
        owns_transaction = not self._connection.in_transaction
        if owns_transaction:
            self._connection.execute("BEGIN")
        try:
            snapshot = self._read_policy_snapshot(data_source_id)
            if owns_transaction:
                self._connection.commit()
            return snapshot
        except Exception:
            if owns_transaction:
                self._connection.rollback()
            raise

    def _read_policy_snapshot(self, data_source_id: str) -> PolicySnapshot:
        source = self.get_public(data_source_id)
        rows = self._connection.execute(
            "SELECT table_name, column_name, access_level FROM data_access_policies "
            "WHERE data_source_id = ?",
            (data_source_id,),
        ).fetchall()
        return PolicySnapshot(
            data_source_id=data_source_id,
            version=source.policy_version,
            rules={
                (row["table_name"], row["column_name"]): AccessLevel(row["access_level"])
                for row in rows
            },
        )

    def _public_row(self, data_source_id: str) -> sqlite3.Row | None:
        return self._connection.execute(
            "SELECT id, project_id, name, dialect, policy_version, created_at, updated_at "
            "FROM data_sources WHERE id = ? AND deleted_at IS NULL",
            (data_source_id,),
        ).fetchone()

    def _recover_stale_attempts_in_transaction(self, data_source_id: str) -> None:
        """Fail only attempts older than two configured statement timeouts.

        This bounded recovery prevents a process crash from permanently fencing
        policy changes while preserving the policy fence for a live dispatch.
        """
        cutoff = (datetime.now(UTC) - self._attempt_stale_after).isoformat()
        self._connection.execute(
            "UPDATE database_query_attempts SET status = 'failed', "
            "error_category = 'stale_attempt', completed_at = ? "
            "WHERE data_source_id = ? AND status = 'in_flight' AND started_at <= ?",
            (utc_now(), data_source_id, cutoff),
        )


def _data_source_from_row(row: sqlite3.Row) -> DataSource:
    return DataSource(
        id=row["id"],
        project_id=row["project_id"],
        name=row["name"],
        dialect=row["dialect"],
        policy_version=row["policy_version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _normalize_identifier(value: str) -> str:
    return value if value.startswith('"') and value.endswith('"') else value.lower()


def _postgres_dsn(value: str) -> str:
    normalized = value.strip()
    try:
        parsed = urlsplit(normalized)
    except ValueError as exc:
        raise ValueError("Data source must use a valid PostgreSQL DSN") from exc
    if parsed.scheme not in {"postgres", "postgresql"} or not (
        parsed.netloc or parsed.path.lstrip("/")
    ):
        raise ValueError("Data source must use a valid PostgreSQL DSN")
    return normalized
