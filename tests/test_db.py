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
    } <= names
    assert db_connection.execute(
        "SELECT version FROM schema_migrations"
    ).fetchall() == [(1,)]
