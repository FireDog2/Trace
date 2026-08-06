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
_SAFE_POSTGRES_FUNCTIONS = frozenset(
    {
        # Aggregation and statistics.
        "array",
        "array_agg",
        "array_size",
        "avg",
        "bit_and",
        "bit_or",
        "bool_and",
        "bool_or",
        "corr",
        "count",
        "covar_pop",
        "covar_samp",
        "every",
        "group_concat",
        "json_agg",
        "json_object_agg",
        "jsonb_agg",
        "jsonb_object_agg",
        "max",
        "min",
        "percentile_cont",
        "percentile_disc",
        "stddev",
        "stddev_pop",
        "stddev_samp",
        "string_agg",
        "sum",
        "logical_and",
        "logical_or",
        "var_pop",
        "var_samp",
        "variance",
        # String and binary transformations.
        "btrim",
        "char_length",
        "concat",
        "concat_ws",
        "encode",
        "initcap",
        "left",
        "length",
        "lower",
        "ltrim",
        "md5",
        "octet_length",
        "position",
        "regexp_replace",
        "repeat",
        "replace",
        "reverse",
        "right",
        "rtrim",
        "split_part",
        "strpos",
        "str_position",
        "substr",
        "substring",
        "trim",
        "upper",
        # Date/time analysis.
        "age",
        "current_date",
        "current_time",
        "current_timestamp",
        "date_part",
        "date_trunc",
        "extract",
        "localtime",
        "localtimestamp",
        "make_date",
        "now",
        "timestamp_trunc",
        "time_to_str",
        "to_char",
        "to_date",
        "str_to_date",
        # Math.
        "abs",
        "cbrt",
        "ceil",
        "ceiling",
        "degrees",
        "exp",
        "floor",
        "ln",
        "log",
        "log10",
        "mod",
        "pi",
        "power",
        "radians",
        "round",
        "sign",
        "sqrt",
        "trunc",
        # Null handling, values, JSON, arrays, and reviewed window functions.
        "array_length",
        "cardinality",
        "cast",
        "coalesce",
        "cume_dist",
        "dense_rank",
        "first_value",
        "greatest",
        "json_build_array",
        "json_build_object",
        "json_extract_path",
        "json_extract_path_text",
        "jsonb_build_array",
        "jsonb_build_object",
        "jsonb_extract_path",
        "jsonb_extract_path_text",
        "lag",
        "last_value",
        "lead",
        "least",
        "nth_value",
        "ntile",
        "nullif",
        "percent_rank",
        "rank",
        "row_number",
        "row_to_json",
        "to_json",
        "to_jsonb",
    }
)


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
        _validate_safe_casts(statement)
        _validate_safe_functions(statement)
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


def _validate_safe_functions(statement: exp.Query) -> None:
    for function in statement.find_all(exp.Func):
        if isinstance(function.parent, exp.Dot) and function.parent.expression is function:
            raise SqlValidationError(
                "unsafe_function",
                "Schema-qualified PostgreSQL functions are not allowed",
            )
        if isinstance(function, exp.Anonymous):
            function_name = function.name.lower()
        else:
            function_name = function.sql_name().lower()
        if function_name not in _SAFE_POSTGRES_FUNCTIONS:
            raise SqlValidationError(
                "unsafe_function",
                f"PostgreSQL function {function_name!r} is not allowed",
            )


def _validate_safe_casts(statement: exp.Query) -> None:
    for data_type in statement.find_all(exp.DataType):
        if data_type.this is exp.DataType.Type.USERDEFINED:
            raise SqlValidationError(
                "unsafe_cast",
                "Custom PostgreSQL cast types are not allowed",
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
    handled_stars: set[int] = set()

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

        for star_reference in scope.stars:
            if not isinstance(star_reference, exp.Column) or not isinstance(
                star_reference.this, exp.Star
            ):
                raise SqlValidationError(
                    "unresolved_star", "Star reference could not be resolved safely"
                )
            source = base_sources.get(star_reference.table)
            if source is not None:
                table_name, columns = source
                for column_name in columns.values():
                    key = (table_name, column_name)
                    if key not in seen_references:
                        references.append(SqlReference(*key))
                        seen_references.add(key)
            elif not isinstance(scope.sources.get(star_reference.table), Scope):
                raise SqlValidationError(
                    "unresolved_star", "Star reference could not be resolved safely"
                )
            handled_stars.add(id(star_reference.this))

    for star in statement.find_all(exp.Star):
        if id(star) in handled_stars:
            continue
        if isinstance(star.parent, exp.Count) and star.parent.this is star:
            continue
        raise SqlValidationError(
            "unresolved_star", "Star reference could not be resolved safely"
        )

    return tuple(tables), tuple(references)


def _resolve_table(
    table: exp.Table,
    catalog_index: dict[str, tuple[str, dict[str, str]]],
) -> tuple[str, dict[str, str]]:
    if table.args.get("db") is not None or table.args.get("catalog") is not None:
        raise SqlValidationError(
            "qualified_table_not_allowed",
            "Schema- and catalog-qualified table names are not supported",
        )
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
