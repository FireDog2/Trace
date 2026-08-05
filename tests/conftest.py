from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from data_analysis_agent.db import migrate


@pytest.fixture
def db_connection() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    migrate(connection)
    try:
        yield connection
    finally:
        connection.close()
