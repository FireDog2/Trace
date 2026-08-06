from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Mapping, Sequence, TypeAlias

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope

from data_analysis_agent.data_sources import AccessLevel, PolicySnapshot


SchemaCatalog: TypeAlias = Mapping[str, Sequence[str]]


@dataclass(frozen=True, slots=True)
class SqlReference:
    table: str
    column: str


@dataclass(frozen=True, slots=True)
class ValidatedSql:
    normalized_sql: str
    sql_hash: str
    tables: tuple[str, ...]
    references: tuple[SqlReference, ...]
    access_level: AccessLevel


class SqlValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


_DENIED_NODE_TYPES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Create,
    exp.Drop,
    exp.Command,
    exp.Merge,
    exp.Alter,
    exp.TruncateTable,
    exp.Into,
)
_ACCESS_STRENGTH = {
    AccessLevel.ALLOWED: 0,
    AccessLevel.APPROVAL_REQUIRED: 1,
    AccessLevel.DENIED: 2,
}
_COLUMN_ERROR = re.compile(r"Column '([^']+)' could not be resolved")


class SqlAstValidator:
    def validate(
        self,
        sql: str,
        catalog: SchemaCatalog,
        policy: PolicySnapshot,
    ) -> ValidatedSql:
        statement = _parse_one_statement(sql)
        catalog_index = _catalog_index(catalog)
        _deny_nested_writes(statement)
        _validate_catalog_tables(statement, catalog_index)

        normalized = statement.sql(dialect="postgres", pretty=False, normalize=True)
        try:
            qualified = qualify(
                statement.copy(),
                dialect="postgres",
                schema=_sqlglot_schema(catalog_index),
                quote_identifiers=False,
                identify=False,
                validate_qualify_columns=True,
            )
        except OptimizeError as exc:
            raise _column_resolution_error(statement, catalog_index, exc) from exc

        tables, references = _base_references(qualified, catalog_index)
        return ValidatedSql(
            normalized_sql=normalized,
            sql_hash=sha256(normalized.encode("utf-8")).hexdigest(),
            tables=tables,
            references=references,
            access_level=_access_level(tables, references, policy),
        )


def _parse_one_statement(sql: str) -> exp.Query:
    try:
        statements = sqlglot.parse(sql, read="postgres")
    except ParseError as exc:
        raise SqlValidationError("parse_error", "SQL could not be parsed") from exc
    if len(statements) != 1 or statements[0] is None:
        raise SqlValidationError(
            "exactly_one_statement", "Exactly one SQL statement is required"
        )
    statement = statements[0]
    if not isinstance(statement, exp.Query):
        raise SqlValidationError(
            "read_only_required", "Only query expressions are allowed"
        )
    return statement


def _deny_nested_writes(statement: exp.Query) -> None:
    for denied_type in _DENIED_NODE_TYPES:
        if next(statement.find_all(denied_type), None) is not None:
            raise SqlValidationError(
                "unsafe_statement", f"{denied_type.__name__} is denied"
            )


def _catalog_index(
    catalog: SchemaCatalog,
) -> dict[str, tuple[str, dict[str, str]]]:
    index: dict[str, tuple[str, dict[str, str]]] = {}
    for table, columns in catalog.items():
        table_name = _catalog_identifier(table)
        column_index = {
            _catalog_identifier(column): _catalog_identifier(column)
            for column in columns
        }
        index[table_name] = (table_name, column_index)
    return index


def _sqlglot_schema(
    catalog_index: dict[str, tuple[str, dict[str, str]]],
) -> dict[str, dict[str, str]]:
    return {
        table: {column: "UNKNOWN" for column in columns}
        for table, (_, columns) in catalog_index.items()
    }


def _validate_catalog_tables(
    statement: exp.Query,
    catalog_index: dict[str, tuple[str, dict[str, str]]],
) -> None:
    for scope in traverse_scope(statement):
        for source in scope.sources.values():
            if isinstance(source, exp.Table):
                _resolve_table(source, catalog_index)


def _base_references(
    statement: exp.Expression,
    catalog_index: dict[str, tuple[str, dict[str, str]]],
) -> tuple[tuple[str, ...], tuple[SqlReference, ...]]:
    tables: list[str] = []
    references: list[SqlReference] = []
    seen_tables: set[str] = set()
    seen_references: set[tuple[str, str]] = set()

    for scope in traverse_scope(statement):
        base_sources: dict[str, tuple[str, dict[str, str]]] = {}
        for alias, source in scope.sources.items():
            if not isinstance(source, exp.Table):
                continue
            table = _resolve_table(source, catalog_index)
            base_sources[alias] = table
            if table[0] not in seen_tables:
                tables.append(table[0])
                seen_tables.add(table[0])

        for column in scope.columns:
            source = base_sources.get(column.table)
            if source is None:
                continue
            table_name, columns = source
            column_name = _identifier(column.this)
            if column_name not in columns:
                raise SqlValidationError(
                    "unresolved_column",
                    f"Column {column_name!r} does not exist on {table_name!r}",
                )
            key = (table_name, columns[column_name])
            if key not in seen_references:
                references.append(SqlReference(*key))
                seen_references.add(key)

    return tuple(tables), tuple(references)


def _resolve_table(
    table: exp.Table,
    catalog_index: dict[str, tuple[str, dict[str, str]]],
) -> tuple[str, dict[str, str]]:
    table_name = _identifier(table.this)
    try:
        return catalog_index[table_name]
    except KeyError as exc:
        raise SqlValidationError(
            "unresolved_table", f"Table {table_name!r} is not in the schema catalog"
        ) from exc


def _column_resolution_error(
    statement: exp.Query,
    catalog_index: dict[str, tuple[str, dict[str, str]]],
    error: OptimizeError,
) -> SqlValidationError:
    message = str(error)
    match = _COLUMN_ERROR.search(message)
    reference = match.group(1) if match else ""
    if "." in reference:
        qualifier = reference.rsplit(".", 1)[0]
        aliases = {
            alias
            for scope in traverse_scope(statement)
            for alias in scope.sources
        }
        code = "unresolved_column" if qualifier in aliases else "unresolved_table"
    else:
        column = _catalog_identifier(reference)
        matches = sum(column in columns for _, columns in catalog_index.values())
        code = "ambiguous_column" if matches > 1 else "unresolved_column"
    return SqlValidationError(code, message or "Column reference could not be resolved")


def _access_level(
    tables: tuple[str, ...],
    references: tuple[SqlReference, ...],
    policy: PolicySnapshot,
) -> AccessLevel:
    levels: list[AccessLevel] = []
    referenced_tables: set[str] = set()
    for reference in references:
        levels.append(policy.resolve(reference.table, reference.column))
        referenced_tables.add(reference.table)
    for table in tables:
        if table not in referenced_tables:
            levels.append(policy.rules.get((table, None), AccessLevel.DENIED))
    return max(levels, key=_ACCESS_STRENGTH.__getitem__, default=AccessLevel.ALLOWED)


def _catalog_identifier(value: str) -> str:
    return value if value.startswith('"') and value.endswith('"') else value.lower()


def _identifier(identifier: exp.Expression) -> str:
    value = identifier.name
    if identifier.args.get("quoted"):
        return f'"{value.replace(chr(34), chr(34) * 2)}"'
    return value.lower()
