import sqlite3
import threading
from pathlib import Path

import pytest

from data_analysis_agent.data_sources import (
    AccessLevel,
    CredentialError,
    DataSourceRepository,
)
from data_analysis_agent.db import migrate
from data_analysis_agent.repositories import (
    ProjectRepository,
    RecordNotFoundError,
    RepositoryError,
)


class RecordingProtector:
    def protect(self, value: str) -> bytes:
        return b"protected:" + value.encode()

    def unprotect(self, value: bytes) -> str:
        return value.removeprefix(b"protected:").decode()


class PauseAfterVersionReadConnection:
    def __init__(
        self,
        connection: sqlite3.Connection,
        version_read: threading.Event,
        replacement_done: threading.Event,
    ) -> None:
        self._connection = connection
        self._version_read = version_read
        self._replacement_done = replacement_done
        self.pause_enabled = False

    @property
    def row_factory(self):
        return self._connection.row_factory

    @row_factory.setter
    def row_factory(self, value) -> None:
        self._connection.row_factory = value

    def execute(self, sql: str, parameters=()):
        cursor = self._connection.execute(sql, parameters)
        if self.pause_enabled and sql.startswith(
            "SELECT id, project_id, name, dialect, policy_version"
        ):
            self.pause_enabled = False
            return PauseAfterFetchCursor(cursor, self._version_read, self._replacement_done)
        return cursor

    def __getattr__(self, name: str):
        return getattr(self._connection, name)


class PauseAfterFetchCursor:
    def __init__(
        self,
        cursor: sqlite3.Cursor,
        version_read: threading.Event,
        replacement_done: threading.Event,
    ) -> None:
        self._cursor = cursor
        self._version_read = version_read
        self._replacement_done = replacement_done

    def fetchone(self):
        row = self._cursor.fetchone()
        self._version_read.set()
        if not self._replacement_done.wait(timeout=5):
            raise AssertionError("Concurrent policy replacement did not finish")
        return row

    def __getattr__(self, name: str):
        return getattr(self._cursor, name)


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


def test_data_source_connection_can_be_renamed_without_revealing_or_replacing_dsn(
    db_connection, project
) -> None:
    repository = DataSourceRepository(db_connection, RecordingProtector())
    source = repository.create(
        project.id,
        "Warehouse",
        "postgresql://analyst:secret@localhost/portfolio",
    )

    renamed = repository.update(source.id, name="Finance Warehouse")

    assert renamed.name == "Finance Warehouse"
    assert not hasattr(renamed, "dsn")
    assert (
        repository.credentials(source.id).dsn == "postgresql://analyst:secret@localhost/portfolio"
    )

    repository.update(
        source.id,
        name="Finance Warehouse",
        dsn="postgresql://reader:new-secret@localhost/finance",
    )
    assert (
        repository.credentials(source.id).dsn == "postgresql://reader:new-secret@localhost/finance"
    )


def test_data_source_can_be_deleted_without_losing_audit_rows_or_credentials(
    db_connection, project
) -> None:
    repository = DataSourceRepository(db_connection, RecordingProtector())
    source = repository.create(
        project.id,
        "Warehouse",
        "postgresql://analyst:secret@localhost/portfolio",
    )
    repository.replace_policies(source.id, {("orders", None): AccessLevel.ALLOWED})

    repository.delete(source.id)

    assert repository.list_for_project(project.id) == ()
    assert repository.list_all() == ()
    with pytest.raises(RecordNotFoundError):
        repository.get_public(source.id)
    with pytest.raises(RecordNotFoundError):
        repository.credentials(source.id)
    with pytest.raises(RecordNotFoundError):
        repository.policy_snapshot(source.id)
    stored = db_connection.execute(
        "SELECT name, protected_dsn, deleted_at FROM data_sources WHERE id = ?",
        (source.id,),
    ).fetchone()
    assert stored["name"] == f"__deleted__:{source.id}"
    assert bytes(stored["protected_dsn"]) == b""
    assert stored["deleted_at"] is not None
    assert (
        db_connection.execute(
            "SELECT COUNT(*) FROM data_access_policies WHERE data_source_id = ?",
            (source.id,),
        ).fetchone()[0]
        == 1
    )

    replacement = repository.create(project.id, "Warehouse", "postgresql://localhost/replacement")
    assert replacement.name == "Warehouse"


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


def test_public_reads_and_project_listing_never_expose_credentials(db_connection, project) -> None:
    repository = DataSourceRepository(db_connection, RecordingProtector())
    first = repository.create(project.id, "Warehouse", "postgresql://localhost/warehouse")
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
    original = repository.replace_policies(source.id, {("orders", None): AccessLevel.ALLOWED})
    db_connection.execute(
        "CREATE TRIGGER reject_broken_policy BEFORE INSERT ON data_access_policies "
        "WHEN NEW.table_name = 'broken' BEGIN "
        "SELECT RAISE(ABORT, 'rejected test policy'); END"
    )

    with pytest.raises(sqlite3.IntegrityError, match="rejected test policy"):
        repository.replace_policies(source.id, {("broken", None): AccessLevel.APPROVAL_REQUIRED})

    restored = repository.policy_snapshot(source.id)
    assert restored.version == original.version
    assert restored.resolve("orders", "amount") is AccessLevel.ALLOWED


def test_replacing_policies_rejects_keys_that_collide_after_normalization(
    db_connection, project
) -> None:
    repository = DataSourceRepository(db_connection, RecordingProtector())
    source = repository.create(project.id, "Warehouse", "postgresql://localhost/db")

    with pytest.raises(ValueError, match="Duplicate data-access policy"):
        repository.replace_policies(
            source.id,
            {
                ("Orders", None): AccessLevel.DENIED,
                ("orders", None): AccessLevel.ALLOWED,
            },
        )

    snapshot = repository.policy_snapshot(source.id)
    assert snapshot.version == 1
    assert snapshot.resolve("orders", "amount") is AccessLevel.DENIED


def test_policy_snapshot_reads_version_and_rules_from_one_database_snapshot(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "concurrent-policies.sqlite3"
    reader_connection = sqlite3.connect(database_path, timeout=5)
    reader_connection.execute("PRAGMA journal_mode = WAL")
    migrate(reader_connection)
    project = ProjectRepository(reader_connection).create("Concurrent policy test")
    version_read = threading.Event()
    replacement_done = threading.Event()
    pausing_connection = PauseAfterVersionReadConnection(
        reader_connection, version_read, replacement_done
    )
    reader_repository = DataSourceRepository(pausing_connection, RecordingProtector())
    source = reader_repository.create(project.id, "Warehouse", "postgresql://localhost/db")
    reader_repository.replace_policies(source.id, {("orders", None): AccessLevel.ALLOWED})
    replacement_errors: list[BaseException] = []

    def replace_from_second_connection() -> None:
        writer_connection = sqlite3.connect(database_path, timeout=5)
        try:
            if not version_read.wait(timeout=5):
                raise AssertionError("Snapshot version read did not occur")
            writer_repository = DataSourceRepository(writer_connection, RecordingProtector())
            writer_repository.replace_policies(source.id, {("orders", None): AccessLevel.DENIED})
        except BaseException as exc:
            replacement_errors.append(exc)
        finally:
            writer_connection.close()
            replacement_done.set()

    replacement = threading.Thread(target=replace_from_second_connection)
    replacement.start()
    pausing_connection.pause_enabled = True
    try:
        snapshot = reader_repository.policy_snapshot(source.id)
    finally:
        replacement.join(timeout=5)
        reader_connection.close()

    assert replacement_errors == []
    assert not replacement.is_alive()
    assert snapshot.version == 2
    assert snapshot.resolve("orders", "amount") is AccessLevel.ALLOWED


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://localhost/portfolio",
        "postgres://localhost/portfolio",
        "postgresql:///portfolio",
    ],
)
def test_create_accepts_postgresql_uri_schemes(db_connection, project, dsn: str) -> None:
    repository = DataSourceRepository(db_connection, RecordingProtector())

    source = repository.create(project.id, "Warehouse", dsn)

    assert repository.credentials(source.id).dsn == dsn


@pytest.mark.parametrize(
    "dsn",
    [
        "mysql://localhost/portfolio",
        "https://localhost/portfolio",
        "localhost/portfolio",
        "postgresql://",
        "",
    ],
)
def test_create_rejects_non_postgresql_dsn_before_storing(db_connection, project, dsn: str) -> None:
    repository = DataSourceRepository(db_connection, RecordingProtector())

    with pytest.raises(ValueError, match="PostgreSQL DSN"):
        repository.create(project.id, "Warehouse", dsn)

    count = db_connection.execute("SELECT COUNT(*) FROM data_sources").fetchone()[0]
    assert count == 0


def test_duplicate_data_source_name_rolls_back_and_raises_repository_error(
    db_connection, project
) -> None:
    repository = DataSourceRepository(db_connection, RecordingProtector())
    first = repository.create(project.id, "Warehouse", "postgresql://localhost/primary")

    with pytest.raises(RepositoryError, match="Unable to create data source"):
        repository.create(project.id, "Warehouse", "postgresql://localhost/duplicate")
    assert repository.list_for_project(project.id) == (first,)
    second = repository.create(project.id, "Ledger", "postgresql://localhost/secondary")
    assert repository.list_for_project(project.id) == (first, second)


def test_create_never_reflects_protector_failure_or_dsn(db_connection, project) -> None:
    secret = "postgresql://analyst:secret@localhost/portfolio"

    class BrokenProtector:
        def protect(self, value: str) -> bytes:
            raise RuntimeError(f"broken {value}")

        def unprotect(self, value: bytes) -> str:
            return value.decode()

    with pytest.raises(CredentialError) as error:
        DataSourceRepository(db_connection, BrokenProtector()).create(
            project.id, "Warehouse", secret
        )

    assert secret not in str(error.value)
