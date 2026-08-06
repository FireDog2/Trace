from __future__ import annotations

import sqlite3

import pytest

from data_analysis_agent import db
from data_analysis_agent.db import migrate


def test_migrate_creates_versioned_core_tables(db_connection) -> None:
    names = {
        row[0]
        for row in db_connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }

    assert {
        "schema_migrations",
        "projects",
        "tasks",
        "messages",
        "plan_revisions",
        "plan_steps",
        "tool_executions",
        "artifacts",
        "artifact_inputs",
        "evidence_links",
        "approvals",
        "task_events",
        "model_configuration",
        "data_sources",
        "data_access_policies",
        "task_query_budget",
            "agent_runs",
            "agent_messages",
            "protected_agent_message_payloads",
    } <= names
    assert db_connection.execute(
        "SELECT version FROM schema_migrations ORDER BY version"
    ).fetchall() == [(1,), (2,), (3,), (4,), (5,), (6,), (7,), (8,), (9,), (10,)]
    agent_run_columns = {row[1] for row in db_connection.execute("PRAGMA table_info(agent_runs)")}
    assert {"turns_used", "deadline_at"} <= agent_run_columns
    for table in ("data_access_policies", "database_query_attempts"):
        assert any(
            row[2] == "data_sources"
            for row in db_connection.execute(f"PRAGMA foreign_key_list({table})")
        )


def test_team_migration_replays_after_missing_version_record(db_connection) -> None:
    db_connection.execute("DELETE FROM schema_migrations WHERE version = 7")
    db_connection.commit()

    migrate(db_connection)

    assert db_connection.execute(
        "SELECT version FROM schema_migrations WHERE version = 7"
    ).fetchone() == (7,)
    assert {row[0] for row in db_connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    )} >= {"agent_runs", "agent_messages"}


def test_turn_deadline_migration_replays_when_its_version_record_is_missing(db_connection) -> None:
    db_connection.execute("DELETE FROM schema_migrations WHERE version = 9")
    db_connection.commit()

    migrate(db_connection)

    assert db_connection.execute("SELECT version FROM schema_migrations WHERE version = 9").fetchone() == (9,)


def test_data_source_migration_stores_encrypted_credentials_only(db_connection) -> None:
    columns = {
        row[1] for row in db_connection.execute("PRAGMA table_info(data_sources)")
    }

    assert "protected_dsn" in columns
    assert "dsn" not in columns
    assert "password" not in columns


def test_model_configuration_is_a_singleton_table(db_connection) -> None:
    columns = {
        row[1]
        for row in db_connection.execute("PRAGMA table_info(model_configuration)")
    }

    assert columns == {
        "singleton_id",
        "provider",
        "base_url",
        "model",
        "protected_api_key",
        "updated_at",
    }


def test_model_configuration_migration_recovers_after_version_record_is_missing(
    db_connection,
) -> None:
    db_connection.execute("DELETE FROM schema_migrations WHERE version = 2")
    db_connection.commit()

    migrate(db_connection)

    assert db_connection.execute(
        "SELECT version FROM schema_migrations WHERE version = 2"
    ).fetchone() == (2,)


def test_data_source_migration_recovers_after_version_record_is_missing(
    db_connection,
) -> None:
    db_connection.execute("DELETE FROM schema_migrations WHERE version = 3")
    db_connection.commit()

    migrate(db_connection)

    assert db_connection.execute(
        "SELECT version FROM schema_migrations WHERE version = 3"
    ).fetchone() == (3,)


def test_query_attempt_migration_is_replay_safe_and_records_durable_state(
    db_connection,
) -> None:
    db_connection.execute("DELETE FROM schema_migrations WHERE version = 4")
    db_connection.commit()

    migrate(db_connection)

    columns = {
        row[1]
        for row in db_connection.execute("PRAGMA table_info(database_query_attempts)")
    }
    assert {
        "id", "task_id", "data_source_id", "policy_version", "sql_hash",
        "approval_id", "status", "error_category", "started_at", "completed_at",
    } <= columns
    assert db_connection.execute(
        "SELECT version FROM schema_migrations WHERE version = 4"
    ).fetchone() == (4,)


@pytest.mark.parametrize("failure", ("pragma", "migrate"))
def test_connect_database_closes_connection_when_initialization_fails(
    settings, monkeypatch, failure
) -> None:
    """A successfully opened connection is never leaked by setup failures."""
    raw = sqlite3.connect(":memory:")

    class TrackingConnection:
        closed = False

        def execute(self, statement, *args):
            if failure == "pragma" and statement == "PRAGMA foreign_keys = ON":
                raise sqlite3.DatabaseError("initialization failed")
            return raw.execute(statement, *args)

        def close(self):
            self.closed = True
            raw.close()

        def __getattr__(self, name):
            return getattr(raw, name)

    connection = TrackingConnection()
    monkeypatch.setattr(db.sqlite3, "connect", lambda *args, **kwargs: connection)
    if failure == "migrate":
        def fail_migrate(_: object) -> None:
            raise sqlite3.DatabaseError("migration failed")

        monkeypatch.setattr(db, "migrate", fail_migrate)

    with pytest.raises(sqlite3.DatabaseError):
        db.connect_database(settings)

    assert connection.closed is True
