from __future__ import annotations


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
    } <= names
    assert db_connection.execute(
        "SELECT version FROM schema_migrations ORDER BY version"
    ).fetchall() == [(1,), (2,)]


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
