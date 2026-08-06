"""Opt-in PostgreSQL checks; they never discover or print a DSN."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from data_analysis_agent.data_sources import AccessLevel, DataSourceRepository
from data_analysis_agent.db import connect_database
from data_analysis_agent.postgres import PsycopgDriver, SqlSafetyGateway
from data_analysis_agent.repositories import ArtifactRepository, ProjectRepository, TaskRepository
from data_analysis_agent.permissions import ApprovalRepository
from data_analysis_agent.settings import Settings
from data_analysis_agent.sql_safety import SqlAstValidator
from data_analysis_agent.workspace import WorkspaceService


@pytest.fixture
def postgres_dsn() -> str:
    dsn = os.environ.get("TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("TEST_POSTGRES_DSN is not set")
    return dsn


class _Protector:
    def protect(self, value: str) -> bytes: return b"test:" + value.encode()[::-1]
    def unprotect(self, value: bytes) -> str: return value.removeprefix(b"test:")[::-1].decode()


def test_live_gateway_read_only_search_path_values_and_timeout(postgres_dsn: str, tmp_path: Path) -> None:
    """Exercise the real gateway only against explicit disposable public objects."""
    token = uuid4().hex
    table, schema, sequence = (f"codex_pg_{token}", f"codex_shadow_{token}", f"codex_seq_{token}")
    try:
        with psycopg.connect(postgres_dsn) as setup:
            with setup.cursor() as cursor:
                cursor.execute(f"CREATE TABLE public.{table} (amount numeric, occurred_at timestamptz, token uuid, payload bytea, restricted text)")
                cursor.execute(f"INSERT INTO public.{table} VALUES (%s, %s, %s, %s, %s)", (Decimal("2.50"), datetime.now(UTC), uuid4(), b"value", "restricted"))
                cursor.execute(f"INSERT INTO public.{table}(amount, occurred_at, token, payload, restricted) SELECT 1, now(), '00000000-0000-0000-0000-000000000001'::uuid, 'x'::bytea, 'restricted' FROM generate_series(1, 1000)")
                cursor.execute(f"CREATE SCHEMA {schema}")
                cursor.execute(f"CREATE TABLE {schema}.{table} (shadow_only text)")
                cursor.execute(f"CREATE SEQUENCE public.{sequence}")

        settings = Settings(data_dir=tmp_path / "data", database_path=tmp_path / "metadata.sqlite3", postgres_statement_timeout_ms=50)
        connection = connect_database(settings)
        workspace = WorkspaceService(settings)
        project = ProjectRepository(connection).create("Live PostgreSQL")
        workspace.create_project(project.id)
        task = TaskRepository(connection).create(project.id, "Verify PostgreSQL")
        sources = DataSourceRepository(connection, _Protector())
        source = sources.create(project.id, "Live", postgres_dsn)
        sources.replace_policies(source.id, {(table, None): AccessLevel.ALLOWED, (table, "restricted"): AccessLevel.APPROVAL_REQUIRED})
        gateway = SqlSafetyGateway(connection=connection, settings=settings, sources=sources, validator=SqlAstValidator(), approvals=ApprovalRepository(connection), artifacts=ArtifactRepository(connection, workspace), workspace=workspace, tasks=TaskRepository(connection))

        catalog = gateway.inspect_schema(source.id)
        assert table in catalog and "shadow_only" not in catalog[table]
        outcome = gateway.execute(task.id, project.id, source.id, f"SELECT amount, occurred_at, token, payload FROM {table}")
        assert outcome.status == "succeeded"
        assert isinstance(outcome.preview[0]["amount"], str)
        assert outcome.preview[0]["payload"].startswith("base64:")

        restricted_sql = f"SELECT sum(length(a.restricted)) FROM {table} AS a CROSS JOIN {table} AS b"
        restricted = gateway.execute(task.id, project.id, source.id, restricted_sql)
        assert restricted.status == "approval_required"
        ApprovalRepository(connection).decide(restricted.approval_id, approved=True)
        gateway.settings = replace(settings, postgres_statement_timeout_ms=5)
        timed_out = gateway.execute(task.id, project.id, source.id, restricted_sql)
        assert timed_out.status == "retryable_error" and timed_out.error_category == "query_timeout"
        assert ApprovalRepository(connection).get(restricted.approval_id).status == "consumed"
        assert connection.execute("SELECT attempts FROM task_query_budget WHERE task_id = ?", (task.id,)).fetchone()[0] == 2
        attempt = connection.execute("SELECT status, error_category FROM database_query_attempts WHERE task_id = ? ORDER BY started_at DESC LIMIT 1", (task.id,)).fetchone()
        assert tuple(attempt) == ("failed", "query_timeout")
        driver = PsycopgDriver()
        with pytest.raises(psycopg.Error):
            driver.query(postgres_dsn, f"SELECT nextval('public.{sequence}')", statement_timeout_ms=50, row_limit=1)
        with pytest.raises(psycopg.errors.QueryCanceled):
            driver.query(postgres_dsn, "SELECT pg_sleep(1)", statement_timeout_ms=50, row_limit=1)
        connection.close()
    finally:
        with psycopg.connect(postgres_dsn, autocommit=True) as cleanup:
            with cleanup.cursor() as cursor:
                cursor.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
                cursor.execute(f"DROP TABLE IF EXISTS public.{table} CASCADE")
                cursor.execute(f"DROP SEQUENCE IF EXISTS public.{sequence} CASCADE")
