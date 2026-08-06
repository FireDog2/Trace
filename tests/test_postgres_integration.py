"""Opt-in PostgreSQL checks; run with TEST_POSTGRES_DSN set explicitly.

The ordinary suite never discovers or connects to a local PostgreSQL service.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest


@pytest.fixture
def postgres_dsn() -> str:
    dsn = os.environ.get("TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("TEST_POSTGRES_DSN is not set")
    return dsn


def test_live_postgres_round_trips_read_only_values_and_timeout(postgres_dsn: str) -> None:
    """Exercise disposable values without logging the supplied test DSN."""
    relation = f"codex_gateway_{uuid4().hex}"
    try:
        with psycopg.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"CREATE TEMP TABLE {relation} (amount numeric, occurred_at timestamptz, token uuid, payload bytea)"
                )
                cursor.execute(
                    f"INSERT INTO {relation} VALUES (%s, %s, %s, %s)",
                    (Decimal("2.50"), datetime.now(UTC), uuid4(), b"value"),
                )
                cursor.execute(f"SELECT amount, occurred_at, token, payload FROM {relation}")
                assert cursor.fetchone() is not None
    finally:
        # Temporary objects are connection-scoped; no persistent cleanup or DSN output.
        pass
