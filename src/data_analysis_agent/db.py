from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from functools import wraps
from threading import Lock, RLock
from typing import Any, Callable, TypeVar

from data_analysis_agent.scope import GLOBAL_PROJECT_ID, GLOBAL_PROJECT_NAME
from data_analysis_agent.settings import Settings

_CONNECTION_LOCKS_GUARD = Lock()
_CONNECTION_LOCKS: dict[int, RLock] = {}
_RESULT = TypeVar("_RESULT")


def sqlite_connection_lock(connection: sqlite3.Connection) -> RLock:
    connection_id = id(connection)
    with _CONNECTION_LOCKS_GUARD:
        return _CONNECTION_LOCKS.setdefault(connection_id, RLock())


def sqlite_connection_guarded(
    method: Callable[..., _RESULT],
) -> Callable[..., _RESULT]:
    """Serialize a repository method on its connection-wide reentrant lock."""

    @wraps(method)
    def guarded(self: Any, *args: Any, **kwargs: Any) -> _RESULT:
        with self._lock:
            return method(self, *args, **kwargs)

    return guarded


MIGRATION_1_SQL = """
CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    objective TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE plan_revisions (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    revision INTEGER NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, revision)
);

CREATE TABLE plan_steps (
    id TEXT PRIMARY KEY,
    plan_revision_id TEXT NOT NULL REFERENCES plan_revisions(id),
    position INTEGER NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(plan_revision_id, position)
);

CREATE TABLE tool_executions (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    step_id TEXT REFERENCES plan_steps(id),
    tool_name TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    status TEXT NOT NULL,
    error_category TEXT,
    error_message TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE artifacts (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    task_id TEXT REFERENCES tasks(id),
    artifact_type TEXT NOT NULL,
    path TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    content_type TEXT,
    producer_execution_id TEXT REFERENCES tool_executions(id),
    created_at TEXT NOT NULL,
    UNIQUE(project_id, path)
);

CREATE TABLE artifact_inputs (
    artifact_id TEXT NOT NULL REFERENCES artifacts(id),
    input_artifact_id TEXT NOT NULL REFERENCES artifacts(id),
    PRIMARY KEY(artifact_id, input_artifact_id)
);

CREATE TABLE evidence_links (
    report_artifact_id TEXT NOT NULL REFERENCES artifacts(id),
    evidence_artifact_id TEXT NOT NULL REFERENCES artifacts(id),
    PRIMARY KEY(report_artifact_id, evidence_artifact_id)
);

CREATE TABLE approvals (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    action_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    decided_at TEXT
);

CREATE TABLE task_events (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, sequence)
);

CREATE INDEX idx_task_events_task_sequence
ON task_events(task_id, sequence);
"""

MIGRATION_2_SQL = """
CREATE TABLE IF NOT EXISTS model_configuration (
    singleton_id INTEGER PRIMARY KEY CHECK(singleton_id = 1),
    provider TEXT NOT NULL,
    base_url TEXT NOT NULL,
    model TEXT NOT NULL,
    protected_api_key BLOB NOT NULL,
    updated_at TEXT NOT NULL
);
"""

MIGRATION_3_SQL = """
CREATE TABLE IF NOT EXISTS data_sources (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    name TEXT NOT NULL,
    dialect TEXT NOT NULL CHECK(dialect = 'postgres'),
    protected_dsn BLOB NOT NULL,
    policy_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, name)
);

CREATE TABLE IF NOT EXISTS data_access_policies (
    id TEXT PRIMARY KEY,
    data_source_id TEXT NOT NULL REFERENCES data_sources(id),
    table_name TEXT NOT NULL,
    column_name TEXT,
    access_level TEXT NOT NULL CHECK(
        access_level IN ('allowed', 'approval_required', 'denied')
    ),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(data_source_id, table_name, column_name)
);

CREATE TABLE IF NOT EXISTS task_query_budget (
    task_id TEXT PRIMARY KEY REFERENCES tasks(id),
    attempts INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
"""

MIGRATION_4_SQL = """
CREATE TABLE IF NOT EXISTS database_query_attempts (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    data_source_id TEXT NOT NULL REFERENCES data_sources(id),
    policy_version INTEGER NOT NULL,
    sql_hash TEXT NOT NULL,
    approval_id TEXT REFERENCES approvals(id),
    status TEXT NOT NULL CHECK(status IN ('in_flight', 'completed', 'failed')),
    error_category TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_database_query_attempts_source_status
ON database_query_attempts(data_source_id, status, started_at);
"""

MIGRATION_5_SQL = """
CREATE TABLE IF NOT EXISTS protected_tool_message_payloads (
    message_id TEXT PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
    protected_content BLOB NOT NULL,
    created_at TEXT NOT NULL
);
"""

# Repair version-3/4 tables created by the first final-review pass without
# source foreign keys.  New databases already have the constraints above.
MIGRATION_6_SQL = """
CREATE TABLE data_access_policies_repaired (
    id TEXT PRIMARY KEY,
    data_source_id TEXT NOT NULL REFERENCES data_sources(id),
    table_name TEXT NOT NULL, column_name TEXT,
    access_level TEXT NOT NULL CHECK(access_level IN ('allowed','approval_required','denied')),
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(data_source_id, table_name, column_name)
);
INSERT INTO data_access_policies_repaired SELECT * FROM data_access_policies;
DROP TABLE data_access_policies;
ALTER TABLE data_access_policies_repaired RENAME TO data_access_policies;
CREATE TABLE database_query_attempts_repaired (
    id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
    data_source_id TEXT NOT NULL REFERENCES data_sources(id), policy_version INTEGER NOT NULL,
    sql_hash TEXT NOT NULL, approval_id TEXT REFERENCES approvals(id),
    status TEXT NOT NULL CHECK(status IN ('in_flight','completed','failed')),
    error_category TEXT, started_at TEXT NOT NULL, completed_at TEXT
);
INSERT INTO database_query_attempts_repaired SELECT * FROM database_query_attempts;
DROP TABLE database_query_attempts;
ALTER TABLE database_query_attempts_repaired RENAME TO database_query_attempts;
CREATE INDEX IF NOT EXISTS idx_database_query_attempts_source_status
ON database_query_attempts(data_source_id, status, started_at);
"""

MIGRATION_7_SQL = """
CREATE TABLE IF NOT EXISTS agent_runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('knowledge', 'sql', 'report')),
    objective TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'created', 'working', 'waiting', 'completed', 'failed', 'cancelled'
    )),
    result_json TEXT,
    error_category TEXT,
    error_message TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0, 1)),
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS agent_messages (
    id TEXT PRIMARY KEY,
    agent_run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_runs_task_status_created
ON agent_runs(task_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_agent_messages_run_created
ON agent_messages(agent_run_id, created_at);
"""

MIGRATION_8_SQL = """
CREATE TABLE IF NOT EXISTS protected_agent_message_payloads (
    agent_message_id TEXT PRIMARY KEY REFERENCES agent_messages(id) ON DELETE CASCADE,
    protected_content BLOB NOT NULL,
    created_at TEXT NOT NULL
);
"""

MIGRATION_9_SQL = """
ALTER TABLE agent_runs ADD COLUMN turns_used INTEGER NOT NULL DEFAULT 0 CHECK(turns_used >= 0);
ALTER TABLE agent_runs ADD COLUMN deadline_at TEXT;
"""

MIGRATION_10_SQL = """
ALTER TABLE agent_runs ADD COLUMN worker_token TEXT;
ALTER TABLE agent_runs ADD COLUMN worker_claimed_at TEXT;
"""

MIGRATION_11_SQL = """
CREATE TABLE IF NOT EXISTS analysis_runs (
    id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
    status TEXT NOT NULL CHECK(status IN ('running','completed','failed','cancelled')),
    accepted INTEGER NOT NULL DEFAULT 0 CHECK(accepted IN (0,1)),
    started_at TEXT NOT NULL, completed_at TEXT, error_category TEXT
);
CREATE TABLE IF NOT EXISTS trace_events (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES analysis_runs(id),
    task_id TEXT NOT NULL REFERENCES tasks(id), agent_id TEXT,
    parent_event_id TEXT REFERENCES trace_events(id), sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL, status TEXT NOT NULL, payload_json TEXT NOT NULL,
    started_at TEXT NOT NULL, finished_at TEXT, duration_ms INTEGER,
    attempt INTEGER NOT NULL DEFAULT 1, error_category TEXT,
    artifact_ids_json TEXT NOT NULL DEFAULT '[]', UNIQUE(run_id, sequence)
);
CREATE INDEX IF NOT EXISTS idx_trace_events_run_sequence ON trace_events(run_id, sequence);
"""

MIGRATION_12_SQL = """
CREATE TABLE IF NOT EXISTS skill_candidates (
    id TEXT PRIMARY KEY, source_run_id TEXT NOT NULL REFERENCES analysis_runs(id),
    name TEXT NOT NULL, version INTEGER NOT NULL, markdown TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('candidate','approved','rejected')),
    enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
    generated_by_model TEXT NOT NULL, reviewed_at TEXT, created_at TEXT NOT NULL,
    UNIQUE(name, version)
);
"""

MIGRATION_13_SQL = """
CREATE TABLE agent_runs_v13 (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('knowledge', 'sql', 'report', 'curator')),
    objective TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'created', 'working', 'waiting', 'completed', 'failed', 'cancelled'
    )),
    result_json TEXT, error_category TEXT, error_message TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0, 1)),
    created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT,
    turns_used INTEGER NOT NULL DEFAULT 0 CHECK(turns_used >= 0),
    deadline_at TEXT, worker_token TEXT, worker_claimed_at TEXT,
    trace_run_id TEXT REFERENCES analysis_runs(id),
    parent_event_id TEXT REFERENCES trace_events(id)
);
INSERT INTO agent_runs_v13(
    id, task_id, role, objective, status, result_json, error_category,
    error_message, cancel_requested, created_at, started_at, completed_at,
    turns_used, deadline_at, worker_token, worker_claimed_at,
    trace_run_id, parent_event_id
)
SELECT id, task_id, role, objective, status, result_json, error_category,
       error_message, cancel_requested, created_at, started_at, completed_at,
       turns_used, deadline_at, worker_token, worker_claimed_at, NULL, NULL
FROM agent_runs;
DROP TABLE agent_runs;
ALTER TABLE agent_runs_v13 RENAME TO agent_runs;
CREATE INDEX IF NOT EXISTS idx_agent_runs_task_status_created
ON agent_runs(task_id, status, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_runs_one_curator_per_trace
ON agent_runs(trace_run_id) WHERE role = 'curator';
"""

MIGRATION_14_SQL = """
CREATE TABLE IF NOT EXISTS business_knowledge_entries (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK(kind IN ('metric', 'term', 'rule')),
    name TEXT NOT NULL,
    definition TEXT NOT NULL,
    formula TEXT,
    dimensions_json TEXT NOT NULL DEFAULT '[]',
    synonyms_json TEXT NOT NULL DEFAULT '[]',
    source TEXT,
    version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_business_knowledge_project_name
ON business_knowledge_entries(project_id, name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_business_knowledge_project_updated
ON business_knowledge_entries(project_id, updated_at);
"""

MIGRATION_15_SQL = """
CREATE TABLE IF NOT EXISTS context_checkpoints (
    id TEXT PRIMARY KEY,
    owner_type TEXT NOT NULL CHECK(owner_type IN ('task','agent')),
    owner_id TEXT NOT NULL,
    through_message_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    source_message_count INTEGER NOT NULL CHECK(source_message_count >= 1),
    before_tokens INTEGER NOT NULL CHECK(before_tokens >= 0),
    after_tokens INTEGER NOT NULL CHECK(after_tokens >= 0),
    reason TEXT NOT NULL CHECK(reason IN ('auto','reactive')),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_context_checkpoints_owner_created
ON context_checkpoints(owner_type, owner_id, created_at);
CREATE TRIGGER IF NOT EXISTS delete_task_context_checkpoints
AFTER DELETE ON tasks BEGIN
    DELETE FROM context_checkpoints WHERE owner_type='task' AND owner_id=OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS delete_agent_context_checkpoints
AFTER DELETE ON agent_runs BEGIN
    DELETE FROM context_checkpoints WHERE owner_type='agent' AND owner_id=OLD.id;
END;
"""


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def connect_database(settings: Settings) -> sqlite3.Connection:
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        settings.database_path,
        timeout=30,
        check_same_thread=False,
    )
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.row_factory = sqlite3.Row
        migrate(connection)
        return connection
    except BaseException:
        connection.close()
        raise


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
    if 1 not in applied:
        connection.executescript(MIGRATION_1_SQL)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (1, utc_now()),
        )
    if 2 not in applied:
        connection.executescript(MIGRATION_2_SQL)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (2, utc_now()),
        )
    if 3 not in applied:
        connection.executescript(MIGRATION_3_SQL)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (3, utc_now()),
        )
    if 4 not in applied:
        connection.executescript(MIGRATION_4_SQL)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (4, utc_now()),
        )
    if 5 not in applied:
        connection.executescript(MIGRATION_5_SQL)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (5, utc_now()),
        )
    if 6 not in applied:
        connection.commit()
        _recover_migration_6_intermediates(connection)
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in (item.strip() for item in MIGRATION_6_SQL.split(";")):
                if statement:
                    connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (6, utc_now()),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    if 7 not in applied:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in (item.strip() for item in MIGRATION_7_SQL.split(";")):
                if statement:
                    connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (7, utc_now()),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    if 8 not in applied:
        connection.executescript(MIGRATION_8_SQL)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (8, utc_now()),
        )
    if 9 not in applied:
        try:
            connection.commit()
            connection.execute("BEGIN IMMEDIATE")
            columns = {row[1] for row in connection.execute("PRAGMA table_info(agent_runs)")}
            if "turns_used" not in columns:
                connection.execute(
                    "ALTER TABLE agent_runs ADD COLUMN turns_used INTEGER NOT NULL DEFAULT 0 CHECK(turns_used >= 0)"
                )
            if "deadline_at" not in columns:
                connection.execute("ALTER TABLE agent_runs ADD COLUMN deadline_at TEXT")
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (9, utc_now()),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    if 10 not in applied:
        try:
            connection.commit()
            connection.execute("BEGIN IMMEDIATE")
            columns = {row[1] for row in connection.execute("PRAGMA table_info(agent_runs)")}
            if "worker_token" not in columns:
                connection.execute("ALTER TABLE agent_runs ADD COLUMN worker_token TEXT")
            if "worker_claimed_at" not in columns:
                connection.execute("ALTER TABLE agent_runs ADD COLUMN worker_claimed_at TEXT")
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (10, utc_now()),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    if 11 not in applied:
        connection.executescript(MIGRATION_11_SQL)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (11, utc_now()),
        )
    if 12 not in applied:
        connection.executescript(MIGRATION_12_SQL)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (12, utc_now()),
        )
    if 13 not in applied:
        connection.commit()
        columns = {row[1] for row in connection.execute("PRAGMA table_info(agent_runs)")}
        if not {"trace_run_id", "parent_event_id"} <= columns:
            connection.execute("PRAGMA foreign_keys = OFF")
            try:
                connection.executescript(MIGRATION_13_SQL)
            finally:
                connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_runs_one_curator_per_trace "
            "ON agent_runs(trace_run_id) WHERE role = 'curator'"
        )
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (13, utc_now()),
        )
    if 14 not in applied:
        connection.executescript(MIGRATION_14_SQL)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (14, utc_now()),
        )
    if 15 not in applied:
        connection.executescript(MIGRATION_15_SQL)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (15, utc_now()),
        )
    if 16 not in applied:
        now = utc_now()
        connection.execute(
            "INSERT OR IGNORE INTO projects(id, name, status, created_at, updated_at) "
            "VALUES (?, ?, 'active', ?, ?)",
            (GLOBAL_PROJECT_ID, GLOBAL_PROJECT_NAME, now, now),
        )
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (16, now),
        )
    if 17 not in applied:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(data_sources)")}
        if "deleted_at" not in columns:
            connection.execute("ALTER TABLE data_sources ADD COLUMN deleted_at TEXT")
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (17, utc_now()),
        )
    connection.execute("PRAGMA foreign_keys = ON")
    connection.commit()


def _recover_migration_6_intermediates(connection: sqlite3.Connection) -> None:
    """Recover a legacy interrupted pre-transaction table rebuild deterministically."""
    for original, repaired in (
        ("data_access_policies", "data_access_policies_repaired"),
        ("database_query_attempts", "database_query_attempts_repaired"),
    ):
        has_original = (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (original,),
            ).fetchone()
            is not None
        )
        has_repaired = (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (repaired,),
            ).fetchone()
            is not None
        )
        if has_repaired and has_original:
            connection.execute(f"DROP TABLE {repaired}")
        elif has_repaired:
            connection.execute(f"ALTER TABLE {repaired} RENAME TO {original}")
    connection.commit()
