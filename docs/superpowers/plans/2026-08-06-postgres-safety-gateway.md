# PostgreSQL Safety Gateway Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one encrypted PostgreSQL data source per project, table/field access policies, AST validation, `EXPLAIN` risk assessment, one-time approvals, and bounded read-only execution.

**Architecture:** Keep the existing SQLite metadata store and introduce focused data-source, SQL-policy, and PostgreSQL execution modules. The model receives a `query_database` tool but never a connection string; the tool delegates to `SqlSafetyGateway`, which produces structured outcomes and persists query results as artifacts.

**Tech Stack:** Python 3.12, FastAPI, SQLite, `sqlglot>=27,<28`, `psycopg[binary]>=3.2,<4`, Pydantic 2, pytest.

## Global Constraints

- Support PostgreSQL only; store credentials with the existing `SecretProtector` abstraction and never return them from an API.
- Permit one parseable PostgreSQL query whose root is a `SELECT` or query-producing CTE; reject writes, DDL, `COPY`, `CALL`, `SET`, and unresolved references.
- Use `EXPLAIN (FORMAT JSON)` only; never run `EXPLAIN ANALYZE` automatically.
- Bind approvals to task, data source, normalized SQL hash, and policy version; consume each approval once.
- Enforce read-only transaction mode, statement timeout, lock timeout, row/byte limits, and a per-task query budget.
- Preserve the existing DuckDB `query_data` tool for uploaded files; the new database tool is named `query_database`.

---

### Task 1: Dependencies and migration 3

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/data_analysis_agent/db.py`
- Modify: `tests/test_db.py`

**Interfaces:**
- Produces metadata tables `data_sources`, `data_access_policies`, and `task_query_budget` for later repositories.
- Produces schema migration version `3`.

- [ ] **Step 1: Write the failing migration test**

Add these assertions to `test_migrate_creates_versioned_core_tables` and add a column test:

```python
assert {
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
```

- [ ] **Step 2: Run the migration test and verify the expected failure**

Run: `python -m pytest tests/test_db.py -v`

Expected: FAIL because migration version 3 and the new tables do not exist.

- [ ] **Step 3: Add dependencies and migration SQL**

Add these project dependencies:

```toml
"psycopg[binary]>=3.2,<4",
"sqlglot>=27,<28",
```

Add `MIGRATION_3_SQL` in `db.py`:

```python
MIGRATION_3_SQL = """
CREATE TABLE data_sources (
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

CREATE TABLE data_access_policies (
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

CREATE TABLE task_query_budget (
    task_id TEXT PRIMARY KEY REFERENCES tasks(id),
    attempts INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
"""
```

Apply it after migration 2 and insert migration version 3 in the same transaction pattern already used by `migrate()`.

- [ ] **Step 4: Run the migration tests**

Run: `python -m pytest tests/test_db.py -v`

Expected: all tests in `tests/test_db.py` PASS.

- [ ] **Step 5: Commit the migration**

```powershell
git add pyproject.toml src/data_analysis_agent/db.py tests/test_db.py
git commit -m "feat: add postgres policy metadata schema"
```

---

### Task 2: Encrypted data-source and access-policy repository

**Files:**
- Create: `src/data_analysis_agent/data_sources.py`
- Create: `tests/test_data_sources.py`
- Modify: `tests/conftest.py`

**Interfaces:**
- Consumes: migration 3 tables and `SecretProtector.protect/unprotect` from `model_config.py`.
- Produces: `AccessLevel`, `DataSource`, `PolicySnapshot`, and `DataSourceRepository`.
- Produces exact methods `create`, `get_public`, `credentials`, `list_for_project`, `replace_policies`, and `policy_snapshot`.

- [ ] **Step 1: Write repository tests**

Create `tests/test_data_sources.py`:

```python
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
```

- [ ] **Step 2: Run the repository tests and verify the expected failure**

Run: `python -m pytest tests/test_data_sources.py -v`

Expected: FAIL because `data_analysis_agent.data_sources` does not exist.

- [ ] **Step 3: Implement the repository and immutable policy snapshot**

Create these public types and signatures:

```python
class AccessLevel(StrEnum):
    ALLOWED = "allowed"
    APPROVAL_REQUIRED = "approval_required"
    DENIED = "denied"


@dataclass(frozen=True, slots=True)
class DataSource:
    id: str
    project_id: str
    name: str
    dialect: str
    policy_version: int
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class DataSourceCredentials:
    id: str
    dsn: str


@dataclass(frozen=True, slots=True)
class PolicySnapshot:
    data_source_id: str
    version: int
    rules: dict[tuple[str, str | None], AccessLevel]

    def resolve(self, table: str, column: str) -> AccessLevel:
        return self.rules.get(
            (table, column),
            self.rules.get((table, None), AccessLevel.DENIED),
        )
```

`replace_policies()` must run `DELETE`, `INSERT`, and `policy_version = policy_version + 1` inside `BEGIN IMMEDIATE`; normalize PostgreSQL identifiers to lowercase unless quoted identifiers are explicitly supplied by the schema catalog.

- [ ] **Step 4: Run repository and migration tests**

Run: `python -m pytest tests/test_data_sources.py tests/test_db.py -v`

Expected: all selected tests PASS.

- [ ] **Step 5: Commit the repository**

```powershell
git add src/data_analysis_agent/data_sources.py tests/test_data_sources.py tests/conftest.py
git commit -m "feat: persist encrypted postgres sources and policies"
```

---

### Task 3: PostgreSQL AST validator

**Files:**
- Create: `src/data_analysis_agent/sql_safety.py`
- Create: `tests/test_sql_safety.py`
- Remove obsolete SQL-prefix checks from: `src/data_analysis_agent/permissions.py`
- Modify: `tests/test_permissions.py`

**Interfaces:**
- Consumes: `PolicySnapshot` from Task 2.
- Produces: `SchemaCatalog`, `SqlReference`, `ValidatedSql`, `SqlValidationError`, and `SqlAstValidator.validate(sql, catalog)`.
- `ValidatedSql` fields are `normalized_sql`, `sql_hash`, `tables`, `references`, and `access_level`.

- [ ] **Step 1: Write adversarial AST tests**

Create tests for allowed and denied syntax:

```python
import pytest

from data_analysis_agent.data_sources import AccessLevel, PolicySnapshot
from data_analysis_agent.sql_safety import SqlAstValidator, SqlValidationError


CATALOG = {
    "orders": ("id", "region", "amount", "customer_email"),
    "customers": ("id", "name"),
}
POLICY = PolicySnapshot(
    data_source_id="source-1",
    version=4,
    rules={
        ("orders", None): AccessLevel.ALLOWED,
        ("orders", "customer_email"): AccessLevel.APPROVAL_REQUIRED,
        ("customers", None): AccessLevel.DENIED,
    },
)


def test_validator_resolves_aliases_and_restricted_columns() -> None:
    result = SqlAstValidator().validate(
        "SELECT o.region, o.customer_email FROM orders AS o", CATALOG, POLICY
    )
    assert result.access_level is AccessLevel.APPROVAL_REQUIRED
    assert {(item.table, item.column) for item in result.references} == {
        ("orders", "region"),
        ("orders", "customer_email"),
    }


def test_validator_expands_star_before_policy_check() -> None:
    result = SqlAstValidator().validate("SELECT * FROM orders", CATALOG, POLICY)
    assert result.access_level is AccessLevel.APPROVAL_REQUIRED
    assert ("orders", "customer_email") in {
        (item.table, item.column) for item in result.references
    }


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; DROP TABLE orders",
        "WITH changed AS (DELETE FROM orders RETURNING *) SELECT * FROM changed",
        "COPY orders TO '/tmp/orders.csv'",
        "SELECT * FROM missing_table",
    ],
)
def test_validator_rejects_unsafe_or_unresolved_sql(sql: str) -> None:
    with pytest.raises(SqlValidationError):
        SqlAstValidator().validate(sql, CATALOG, POLICY)
```

- [ ] **Step 2: Run AST tests and verify the expected failure**

Run: `python -m pytest tests/test_sql_safety.py -v`

Expected: FAIL because the validator module does not exist.

- [ ] **Step 3: Implement strict parsing, reference resolution, and hashing**

Implement with these core rules:

```python
statements = sqlglot.parse(sql, read="postgres")
if len(statements) != 1 or statements[0] is None:
    raise SqlValidationError("exactly_one_statement", "Exactly one SQL statement is required")
statement = statements[0]
if not isinstance(statement, exp.Query):
    raise SqlValidationError("read_only_required", "Only query expressions are allowed")
for denied_type in (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Command):
    if next(statement.find_all(denied_type), None) is not None:
        raise SqlValidationError("unsafe_statement", f"{denied_type.__name__} is denied")
normalized = statement.sql(dialect="postgres", pretty=False, normalize=True)
sql_hash = sha256(normalized.encode("utf-8")).hexdigest()
```

Build an alias-to-table map from `exp.Table` nodes, reject catalog misses, resolve qualified and unqualified `exp.Column` nodes, expand `exp.Star` from the catalog, and choose the strongest access level using `DENIED > APPROVAL_REQUIRED > ALLOWED`.

Replace `_is_read_only_sql` and its regular-expression imports in `permissions.py`; `PermissionPolicy` should classify `query_database` as automatic at the generic tool layer because SQL-specific permission is now enforced by the gateway.

- [ ] **Step 4: Run AST and generic permission tests**

Run: `python -m pytest tests/test_sql_safety.py tests/test_permissions.py -v`

Expected: all selected tests PASS, including the existing DuckDB mutation-denial test after it is rewritten to call the AST validator rather than the removed prefix helper.

- [ ] **Step 5: Commit the AST validator**

```powershell
git add src/data_analysis_agent/sql_safety.py src/data_analysis_agent/permissions.py tests/test_sql_safety.py tests/test_permissions.py
git commit -m "feat: validate postgres queries with sql ast"
```

---

### Task 4: Explain risk, bounded execution, and query artifacts

**Files:**
- Create: `src/data_analysis_agent/postgres.py`
- Modify: `src/data_analysis_agent/sql_safety.py`
- Modify: `src/data_analysis_agent/settings.py`
- Create: `tests/test_postgres_gateway.py`
- Modify: `tests/conftest.py`

**Interfaces:**
- Consumes: `DataSourceRepository`, `SqlAstValidator`, `ApprovalRepository`, `ArtifactRepository`, `WorkspaceService`, and `TaskRepository`.
- Produces: `PostgresDriver` protocol, `PsycopgDriver`, `ExplainRisk`, `SqlGatewayOutcome`, and `SqlSafetyGateway.execute(task_id, project_id, data_source_id, sql)`.
- `SqlGatewayOutcome.status` is one of `succeeded`, `approval_required`, `denied`, or `retryable_error`.

- [ ] **Step 1: Write gateway tests with a deterministic fake driver**

```python
class FakePostgresDriver:
    catalog = {"orders": ("region", "amount", "customer_email")}

    def inspect_schema(self, dsn: str):
        return self.catalog

    def explain(self, dsn: str, sql: str, *, statement_timeout_ms: int):
        return {"Plan": {"Total Cost": 42.0, "Plan Rows": 12, "Node Type": "Seq Scan"}}

    def query(self, dsn: str, sql: str, *, statement_timeout_ms: int, row_limit: int):
        return (("region", "amount"), (("East", 10),))


def test_restricted_query_requests_exact_one_time_approval(sql_gateway, source_repository, task):
    source = source_repository.list_for_project(task.project_id)[0]
    first = sql_gateway.execute(
        task.id, task.project_id, source.id,
        "SELECT region, customer_email FROM orders",
    )
    assert first.status == "approval_required"

    sql_gateway.approvals.decide(first.approval_id, approved=True)
    second = sql_gateway.execute(
        task.id, task.project_id, source.id,
        "SELECT region, customer_email FROM orders",
    )
    assert second.status == "succeeded"

    third = sql_gateway.execute(
        task.id, task.project_id, source.id,
        "SELECT region, customer_email FROM orders",
    )
    assert third.status == "approval_required"


def test_timeout_is_structured_and_retryable(sql_gateway, fake_driver, task):
    fake_driver.query_error = TimeoutError("statement timeout")
    outcome = sql_gateway.execute(task.id, task.project_id, "source-1", "SELECT region FROM orders")
    assert outcome.status == "retryable_error"
    assert outcome.error_category == "query_timeout"
    assert outcome.retryable is True
```

- [ ] **Step 2: Run gateway tests and verify the expected failure**

Run: `python -m pytest tests/test_postgres_gateway.py -v`

Expected: FAIL because `PostgresDriver` and `SqlSafetyGateway` do not exist.

- [ ] **Step 3: Implement risk classification and settings**

Add settings with these defaults:

```python
postgres_statement_timeout_ms: int = 8_000
postgres_lock_timeout_ms: int = 1_000
postgres_max_rows: int = 1_000
postgres_max_result_bytes: int = 2_000_000
postgres_max_queries_per_task: int = 8
postgres_high_cost: float = 100_000.0
postgres_extreme_cost: float = 1_000_000.0
```

`ExplainRiskPolicy.classify(plan)` must return low, medium, high, or extreme using total cost, estimated rows, sequential scans, and join count. High risk creates a `ToolAction("query_database", {"data_source_id", "sql_hash", "policy_version"})`; extreme risk returns `denied` without creating an approval.

- [ ] **Step 4: Implement the psycopg driver and artifact persistence**

`PsycopgDriver.query()` must begin a transaction and execute:

```sql
SET TRANSACTION READ ONLY;
SET LOCAL statement_timeout = %s;
SET LOCAL lock_timeout = %s;
```

Wrap the validated query as `SELECT * FROM (<normalized query>) AS safe_query LIMIT %s`, serialize results to `derived/postgres-query-<hash-prefix>.csv`, register a `DERIVED_DATASET` artifact, and reject serialized results over `postgres_max_result_bytes`.

Map psycopg query-cancel exceptions to `query_timeout`, connection exceptions to `connection_error`, result overflow to `result_limit`, and query-budget exhaustion to non-retryable `query_budget_exhausted`. Never return the DSN in diagnostics.

When the gateway needs approval, create the exact `ToolAction`, transition the parent task to `NEEDS_INPUT`, and append `approval.requested` with approval ID, SQL hash, policy version, and safe reason. Add `ApprovalRepository.list_for_task(task_id)` for API and acceptance-test inspection; it must return approvals in creation order without exposing protected data-source credentials.

- [ ] **Step 5: Run gateway, artifact, and existing execution tests**

Run: `python -m pytest tests/test_postgres_gateway.py tests/test_execution.py tests/test_permissions.py -v`

Expected: all selected tests PASS.

- [ ] **Step 6: Commit the gateway**

```powershell
git add src/data_analysis_agent/postgres.py src/data_analysis_agent/sql_safety.py src/data_analysis_agent/settings.py tests/test_postgres_gateway.py tests/conftest.py
git commit -m "feat: execute bounded postgres queries"
```

---

### Task 5: Data-source APIs and `query_database` tool

**Files:**
- Modify: `src/data_analysis_agent/api.py`
- Modify: `src/data_analysis_agent/tools.py`
- Modify: `src/data_analysis_agent/permissions.py`
- Modify: `tests/test_api.py`
- Modify: `tests/test_tools.py`

**Interfaces:**
- Consumes: Task 2 repository and Task 4 gateway.
- Produces API routes `GET/POST /api/projects/{project_id}/data-sources`, `PUT /api/data-sources/{source_id}/policies`, and `GET /api/data-sources/{source_id}/schema`.
- Produces model tool `query_database(data_source_id: str, sql: str)`.

- [ ] **Step 1: Write failing API tests that prove secrets are absent**

```python
def test_data_source_api_never_returns_dsn(settings) -> None:
    with TestClient(create_app(settings, secret_protector=RecordingProtector())) as client:
        project = client.post("/api/projects", json={"name": "Review"}).json()
        response = client.post(
            f"/api/projects/{project['id']}/data-sources",
            json={
                "name": "Warehouse",
                "dsn": "postgresql://analyst:secret@localhost/portfolio",
            },
        )
        assert response.status_code == 201
        assert "dsn" not in response.json()
        assert "secret" not in response.text


def test_policy_api_replaces_rules_and_returns_new_version(client_with_source) -> None:
    source_id = client_with_source.source_id
    response = client_with_source.put(
        f"/api/data-sources/{source_id}/policies",
        json={"rules": [
            {"table": "orders", "column": None, "access": "allowed"},
            {"table": "orders", "column": "email", "access": "approval_required"},
        ]},
    )
    assert response.status_code == 200
    assert response.json()["version"] == 2
```

- [ ] **Step 2: Write the failing tool test**

```python
def test_registry_exposes_database_query_tool(tool_registry) -> None:
    assert "query_database" in {schema.name for schema in tool_registry.schemas()}


def test_query_database_returns_structured_gateway_outcome(database_tool_registry, tool_context) -> None:
    result = database_tool_registry.execute(
        ToolCall(
            id="database-call",
            name="query_database",
            arguments={"data_source_id": "source-1", "sql": "SELECT region FROM orders"},
        ),
        tool_context,
    )
    assert result.success is True
    assert result.output["status"] == "succeeded"
```

- [ ] **Step 3: Run API and tool tests and verify the expected failure**

Run: `python -m pytest tests/test_api.py tests/test_tools.py -v`

Expected: FAIL because the routes and tool are absent.

- [ ] **Step 4: Wire services, request models, routes, and tool handler**

Add `DataSourceRepository` and `SqlSafetyGateway` fields to `AppServices`. Add `QueryDatabaseInput`:

```python
class QueryDatabaseInput(ToolInput):
    data_source_id: str
    sql: str = Field(min_length=1, max_length=50_000)
```

The handler calls `gateway.execute(context.task_id, context.project_id, arguments.data_source_id, arguments.sql)`. Extend `ToolHandlerError` with `pauses_task: bool = False` and propagate it into the returned `ToolResult`. Map `approval_required` to `ToolHandlerError(category="approval_required", retryable=False, pauses_task=True)`, `denied` to non-retryable `ToolHandlerError`, `retryable_error` to retryable `ToolHandlerError`, and `succeeded` to a `HandlerResult` containing preview, row count, risk, query hash, and artifact ID.

Do not create a second generic approval in `ToolRegistry`: the gateway owns database approval creation and consumption.

- [ ] **Step 5: Run the backend suite**

Run: `python -m pytest -v`

Expected: all backend tests PASS.

- [ ] **Step 6: Commit the vertical slice**

```powershell
git add src/data_analysis_agent/api.py src/data_analysis_agent/tools.py src/data_analysis_agent/permissions.py tests/test_api.py tests/test_tools.py
git commit -m "feat: expose governed postgres analysis"
```
