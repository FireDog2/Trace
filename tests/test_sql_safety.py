from __future__ import annotations

from hashlib import sha256

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
    assert result.tables == ("orders",)


def test_validator_expands_star_before_policy_check() -> None:
    result = SqlAstValidator().validate("SELECT * FROM orders", CATALOG, POLICY)

    assert result.access_level is AccessLevel.APPROVAL_REQUIRED
    assert {(item.table, item.column) for item in result.references} == {
        ("orders", "id"),
        ("orders", "region"),
        ("orders", "amount"),
        ("orders", "customer_email"),
    }


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("SELECT 1; DROP TABLE orders", "exactly_one_statement"),
        (
            "WITH changed AS (DELETE FROM orders RETURNING *) SELECT * FROM changed",
            "unsafe_statement",
        ),
        ("COPY orders TO '/tmp/orders.csv'", "read_only_required"),
        ("SELECT * FROM missing_table", "unresolved_table"),
        ("SELECT missing_column FROM orders", "unresolved_column"),
        (
            "SELECT id FROM orders JOIN customers ON orders.id = customers.id",
            "ambiguous_column",
        ),
        ("SELECT absent.id FROM orders", "unresolved_table"),
    ],
)
def test_validator_rejects_unsafe_or_unresolved_sql(sql: str, code: str) -> None:
    with pytest.raises(SqlValidationError) as error:
        SqlAstValidator().validate(sql, CATALOG, POLICY)

    assert error.value.code == code


@pytest.mark.parametrize(
    "sql",
    [
        "WITH changed AS (UPDATE orders SET amount = 0 RETURNING *) SELECT * FROM changed",
        "WITH changed AS (INSERT INTO orders (id) VALUES (1) RETURNING *) SELECT * FROM changed",
        "WITH changed AS (DELETE FROM orders RETURNING *) SELECT * FROM changed",
    ],
)
def test_validator_rejects_nested_writes(sql: str) -> None:
    with pytest.raises(SqlValidationError, match="denied") as error:
        SqlAstValidator().validate(sql, CATALOG, POLICY)

    assert error.value.code == "unsafe_statement"


def test_validator_resolves_unique_unqualified_columns_and_join_aliases() -> None:
    result = SqlAstValidator().validate(
        "SELECT region, c.name FROM orders o JOIN customers c ON o.id = c.id",
        CATALOG,
        POLICY,
    )

    assert {(item.table, item.column) for item in result.references} == {
        ("orders", "region"),
        ("orders", "id"),
        ("customers", "id"),
        ("customers", "name"),
    }
    assert result.access_level is AccessLevel.DENIED


def test_validator_resolves_cte_columns_to_base_table_references() -> None:
    result = SqlAstValidator().validate(
        "WITH regional AS (SELECT region, amount FROM orders) "
        "SELECT region FROM regional WHERE amount > 10",
        CATALOG,
        POLICY,
    )

    assert {(item.table, item.column) for item in result.references} == {
        ("orders", "region"),
        ("orders", "amount"),
    }
    assert result.tables == ("orders",)
    assert result.access_level is AccessLevel.ALLOWED


def test_validator_expands_qualified_star_through_cte() -> None:
    result = SqlAstValidator().validate(
        "WITH visible AS (SELECT o.* FROM orders AS o) SELECT visible.* FROM visible",
        CATALOG,
        POLICY,
    )

    assert ("orders", "customer_email") in {
        (item.table, item.column) for item in result.references
    }
    assert result.access_level is AccessLevel.APPROVAL_REQUIRED


def test_validator_expands_nested_qualified_star_before_policy_check() -> None:
    result = SqlAstValidator().validate(
        "SELECT row_to_json(o.*) FROM orders AS o", CATALOG, POLICY
    )

    assert result.access_level is AccessLevel.APPROVAL_REQUIRED
    assert {(item.table, item.column) for item in result.references} == {
        ("orders", "id"),
        ("orders", "region"),
        ("orders", "amount"),
        ("orders", "customer_email"),
    }


def test_validator_allows_count_star_without_column_access() -> None:
    result = SqlAstValidator().validate("SELECT count(*) FROM orders", CATALOG, POLICY)

    assert result.access_level is AccessLevel.ALLOWED
    assert result.references == ()


def test_validator_rejects_unqualified_nested_star() -> None:
    with pytest.raises(SqlValidationError) as error:
        SqlAstValidator().validate("SELECT row_to_json(*) FROM orders", CATALOG, POLICY)

    assert error.value.code == "unresolved_star"


def test_validator_rejects_unresolved_whole_row_reference() -> None:
    with pytest.raises(SqlValidationError):
        SqlAstValidator().validate("SELECT row_to_json(o) FROM orders AS o", CATALOG, POLICY)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM reporting.orders",
        "SELECT * FROM analytics.reporting.orders",
    ],
)
def test_validator_rejects_qualified_relation_names(sql: str) -> None:
    with pytest.raises(SqlValidationError) as error:
        SqlAstValidator().validate(sql, CATALOG, POLICY)

    assert error.value.code == "qualified_table_not_allowed"


def test_validator_preserves_quoted_mixed_case_identifier_markers() -> None:
    catalog = {'"OrderLines"': ('"ItemID"', '"UnitPrice"')}
    policy = PolicySnapshot(
        data_source_id="source-quoted",
        version=2,
        rules={
            ('"OrderLines"', None): AccessLevel.ALLOWED,
            ('"OrderLines"', '"UnitPrice"'): AccessLevel.APPROVAL_REQUIRED,
        },
    )

    result = SqlAstValidator().validate(
        'SELECT ol."ItemID", ol."UnitPrice" FROM "OrderLines" AS ol',
        catalog,
        policy,
    )

    assert result.tables == ('"OrderLines"',)
    assert {(item.table, item.column) for item in result.references} == {
        ('"OrderLines"', '"ItemID"'),
        ('"OrderLines"', '"UnitPrice"'),
    }
    assert result.access_level is AccessLevel.APPROVAL_REQUIRED


def test_validator_uses_strongest_policy_result() -> None:
    result = SqlAstValidator().validate(
        "SELECT o.customer_email, c.name "
        "FROM orders o CROSS JOIN customers c",
        CATALOG,
        POLICY,
    )

    assert result.access_level is AccessLevel.DENIED


def test_validator_returns_stable_normalized_sql_and_hash() -> None:
    result = SqlAstValidator().validate(
        "select O.REGION from ORDERS as O", CATALOG, POLICY
    )

    assert result.normalized_sql == "SELECT o.region FROM orders AS o"
    assert result.sql_hash == sha256(result.normalized_sql.encode("utf-8")).hexdigest()


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT dblink_exec('warehouse', 'DELETE FROM orders')",
        "SELECT pg_terminate_backend(123)",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT set_config('statement_timeout', '0', false)",
        "SELECT unreviewed_business_function(amount) FROM orders",
    ],
)
def test_validator_rejects_dangerous_or_unknown_postgres_functions(sql: str) -> None:
    with pytest.raises(SqlValidationError) as error:
        SqlAstValidator().validate(sql, CATALOG, POLICY)

    assert error.value.code == "unsafe_function"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT SUM(amount), AVG(amount), MIN(amount), MAX(amount), COUNT(*) FROM orders",
        "SELECT LOWER(region), UPPER(region), LENGTH(region), "
        "CONCAT(region, '-review') FROM orders",
        "SELECT DATE_TRUNC('month', CURRENT_DATE), "
        "EXTRACT(YEAR FROM CURRENT_DATE) FROM orders",
        "SELECT ABS(amount), ROUND(amount, 2), SQRT(amount), POWER(amount, 2) "
        "FROM orders",
        "SELECT COALESCE(region, 'unknown'), NULLIF(amount, 0), "
        "ROW_NUMBER() OVER (ORDER BY amount), LAG(amount) OVER (ORDER BY amount) "
        "FROM orders",
    ],
)
def test_validator_allows_reviewed_read_only_analysis_functions(sql: str) -> None:
    result = SqlAstValidator().validate(sql, CATALOG, POLICY)

    assert result.access_level is AccessLevel.ALLOWED
