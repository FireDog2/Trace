import sqlite3

import pytest

from data_analysis_agent.data_sources import AccessLevel, DataSourceRepository


class RecordingProtector:
    def protect(self, value: str) -> bytes:
        return b"protected:" + value.encode()

    def unprotect(self, value: bytes) -> str:
        return value.removeprefix(b"protected:").decode()


def test_data_source_hides_dsn_and_round_trips_credentials(db_connection, project) -> None:
    repository = DataSourceRepository(db_connection, RecordingProtector())
    source = repository.create(
        project.id,
        "Warehouse",
        "postgresql://analyst:secret@localhost/portfolio",
    )

    assert source.name == "Warehouse"
    assert not hasattr(source, "dsn")
    assert repository.credentials(source.id).dsn.endswith("/portfolio")
    stored = db_connection.execute(
        "SELECT protected_dsn FROM data_sources WHERE id = ?", (source.id,)
    ).fetchone()[0]
    assert b"secret" in stored
    assert not isinstance(stored, str)


def test_replacing_policies_increments_version(db_connection, project) -> None:
    repository = DataSourceRepository(db_connection, RecordingProtector())
    source = repository.create(project.id, "Warehouse", "postgresql://localhost/db")

    snapshot = repository.replace_policies(
        source.id,
        {
            ("orders", None): AccessLevel.ALLOWED,
            ("orders", "customer_email"): AccessLevel.APPROVAL_REQUIRED,
            ("payroll", None): AccessLevel.DENIED,
        },
    )

    assert snapshot.version == 2
    assert snapshot.resolve("orders", "amount") is AccessLevel.ALLOWED
    assert snapshot.resolve("orders", "customer_email") is AccessLevel.APPROVAL_REQUIRED
    assert snapshot.resolve("payroll", "salary") is AccessLevel.DENIED
    assert snapshot.resolve("unknown", "id") is AccessLevel.DENIED


def test_public_reads_and_project_listing_never_expose_credentials(
    db_connection, project
) -> None:
    repository = DataSourceRepository(db_connection, RecordingProtector())
    first = repository.create(
        project.id, "Warehouse", "postgresql://localhost/warehouse"
    )
    second = repository.create(project.id, "Ledger", "postgresql://localhost/ledger")

    public = repository.get_public(first.id)
    listed = repository.list_for_project(project.id)

    assert public == first
    assert listed == (first, second)
    assert all(not hasattr(source, "dsn") for source in listed)


def test_policy_snapshot_is_immutable_and_normalizes_postgres_identifiers(
    db_connection, project
) -> None:
    repository = DataSourceRepository(db_connection, RecordingProtector())
    source = repository.create(project.id, "Warehouse", "postgresql://localhost/db")

    snapshot = repository.replace_policies(
        source.id,
        {
            ("Orders", None): AccessLevel.ALLOWED,
            ('"AuditLog"', None): AccessLevel.APPROVAL_REQUIRED,
        },
    )

    assert snapshot.resolve("orders", "amount") is AccessLevel.ALLOWED
    assert snapshot.resolve('"AuditLog"', "event") is AccessLevel.APPROVAL_REQUIRED
    assert snapshot.resolve("auditlog", "event") is AccessLevel.DENIED
    with pytest.raises(TypeError):
        snapshot.rules[("orders", None)] = AccessLevel.DENIED


def test_replacing_policies_rolls_back_delete_and_version_on_insert_failure(
    db_connection, project
) -> None:
    repository = DataSourceRepository(db_connection, RecordingProtector())
    source = repository.create(project.id, "Warehouse", "postgresql://localhost/db")
    original = repository.replace_policies(
        source.id, {("orders", None): AccessLevel.ALLOWED}
    )
    db_connection.execute(
        "CREATE TRIGGER reject_broken_policy BEFORE INSERT ON data_access_policies "
        "WHEN NEW.table_name = 'broken' BEGIN "
        "SELECT RAISE(ABORT, 'rejected test policy'); END"
    )

    with pytest.raises(sqlite3.IntegrityError, match="rejected test policy"):
        repository.replace_policies(
            source.id, {("broken", None): AccessLevel.APPROVAL_REQUIRED}
        )

    restored = repository.policy_snapshot(source.id)
    assert restored.version == original.version
    assert restored.resolve("orders", "amount") is AccessLevel.ALLOWED
