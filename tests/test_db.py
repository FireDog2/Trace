from __future__ import annotations

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
    } <= names
    assert db_connection.execute(
        "SELECT version FROM schema_migrations ORDER BY version"
    ).fetchall() == [(1,), (2,), (3,)]


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
