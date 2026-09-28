"""Shared SQL capability boundaries for database-backed product runtimes."""

from __future__ import annotations

import re

from sqlglot import exp, parse
from sqlglot.errors import SqlglotError


class ReadSqlValidationError(ValueError):
    """A statement is outside the product's read-only SQL capability."""


# PostgreSQL catalog/introspection functions used by built-in DBA skills.
# Unknown routines remain denied because a UDF can cause effects outside the
# current database transaction even when that transaction is read-only.
POSTGRES_SAFE_DIAGNOSTIC_FUNCTIONS = frozenset(
    {
        "age",
        "current_setting",
        "format_type",
        "has_database_privilege",
        "has_schema_privilege",
        "has_sequence_privilege",
        "has_table_privilege",
        "jsonb_object_keys",
        "obj_description",
        "pg_blocking_pids",
        "pg_current_wal_lsn",
        "pg_get_constraintdef",
        "pg_get_expr",
        "pg_get_functiondef",
        "pg_get_indexdef",
        "pg_get_ruledef",
        "pg_get_triggerdef",
        "pg_get_userbyid",
        "pg_is_in_recovery",
        "pg_last_wal_receive_lsn",
        "pg_last_wal_replay_lsn",
        "pg_last_xact_replay_timestamp",
        "pg_relation_size",
        "pg_size_pretty",
        "pg_table_size",
        "pg_total_relation_size",
        "pg_wal_lsn_diff",
    }
)


def _safe_anonymous_functions(dialect: str) -> frozenset[str]:
    return POSTGRES_SAFE_DIAGNOSTIC_FUNCTIONS if dialect == "postgres" else frozenset()


def validate_read_sql(sql: str, dialect: str) -> None:
    """Accept one read statement and reject executable routine escapes."""
    if re.search(r"/\*(?:!|M!)", sql, re.IGNORECASE):
        raise ReadSqlValidationError("Executable SQL comments are not supported by this read tool.")
    try:
        statements = parse(sql, read=dialect)
    except SqlglotError as exc:
        raise ReadSqlValidationError(
            "SQL could not be parsed; provide one read-only query."
        ) from exc
    if len(statements) != 1:
        raise ReadSqlValidationError(
            f"query_database accepts exactly one SQL statement per call, but received "
            f"{len(statements)}. Submit each read-only SELECT/WITH query, SHOW, or DESCRIBE "
            "as a separate query_database call."
        )
    statement = statements[0]
    if not isinstance(statement, (exp.Query, exp.Show, exp.Describe)):
        raise ReadSqlValidationError(
            "query_database accepts exactly one read-only SELECT/WITH query, SHOW, or "
            "DESCRIBE statement. Use request_database_change for writes or schema changes."
        )
    if any(isinstance(node, (exp.DML, exp.DDL, exp.Into, exp.Lock)) for node in statement.walk()):
        raise ReadSqlValidationError(
            "Writes, SELECT INTO, and locking clauses are not authorized by this read tool."
        )
    allowed_functions = _safe_anonymous_functions(dialect)
    rejected = sorted(
        {
            str(node.name or "").casefold()
            for node in statement.walk()
            if isinstance(node, exp.Anonymous)
            and str(node.name or "").casefold() not in allowed_functions
        }
    )
    if rejected:
        raise ReadSqlValidationError(
            "Custom or unrecognized SQL functions are not available in the read-only tool: "
            + ", ".join(rejected)
        )


def datasource_dialect(db_type: str | None) -> str:
    """Return the sqlglot dialect used by a saved datasource."""
    normalized = (db_type or "").strip().lower()
    return "postgres" if normalized in {"postgres", "postgresql"} else "mysql"
