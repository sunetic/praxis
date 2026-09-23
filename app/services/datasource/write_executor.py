"""Single approved SQL statements on fresh connections, with no retry or replay.

Preparing on the server also enforces the single-statement boundary independently
of the client parser. No transaction/rollback claim is made for arbitrary DDL or
nontransactional tables. Losing the execution reply leaves effects unknown.
"""

import asyncio
import re

import aiomysql
import asyncpg
from sqlglot import exp, parse
from sqlglot.errors import SqlglotError


class WriteNotExecutedError(Exception):
    """Connection or statement preparation failed before EXECUTE was sent."""

    def __init__(self, detail: str, *, stage: str):
        super().__init__(detail)
        self.stage = stage


class WriteOutcomeUnknownError(Exception):
    """Execution started but no successful completion receipt was received."""


def validate_write_sql(sql: str, dialect: str) -> None:
    if re.search(r"/\*(?:!|M!)", sql, re.IGNORECASE):
        raise ValueError("Executable SQL comments are not supported.")
    try:
        statements = parse(sql, read=dialect)
    except SqlglotError as exc:
        raise ValueError("Provide one parsable SQL write statement.") from exc
    accepted = (exp.DML, exp.DDL, exp.Alter, exp.Drop, exp.TruncateTable, exp.Grant, exp.Revoke)
    if len(statements) != 1 or not isinstance(statements[0], accepted):
        raise ValueError(
            "This tool accepts one data or schema change, GRANT or REVOKE. "
            "Queries, session/transaction control, procedures and SQL scripts are not supported."
        )


async def execute_write(datasource, sql: str) -> dict:
    """Return a server completion receipt, never infer the user's goal was met."""
    engine = (datasource.db_type or "mysql").lower()
    if engine not in {"mysql", "oceanbase", "postgres", "postgresql"}:
        raise WriteNotExecutedError("Unsupported database engine", stage="validation")
    postgres = engine in {"postgres", "postgresql"}
    validate_write_sql(sql, "postgres" if postgres else "mysql")
    connection = None
    dispatched = False
    stage = "connection"
    try:
        credentials = dict(
            host=datasource.host,
            port=datasource.port,
            user=datasource.user or "",
            password=datasource.password or "",
        )
        if postgres:
            connection = await asyncpg.connect(
                **credentials, database=datasource.database or "", timeout=15
            )
            # PostgreSQL rejects multi-command prepared statements. The exact
            # same SQL is then executed on this connection; execute discards
            # RETURNING rows instead of buffering an unbounded result.
            stage = "statement_preparation"
            await connection.prepare(sql)
            dispatched = True
            status = await connection.execute(sql)
            return {"command_status": status, "returned_rows": False}
        connection = await aiomysql.connect(
            **credentials,
            db=datasource.database or "",
            connect_timeout=15,
            autocommit=True,
            local_infile=False,
            charset="utf8mb4",
        )
        cursor = await connection.cursor(aiomysql.SSCursor)
        # Do not send raw SQL through aiomysql's MULTI_STATEMENTS connection.
        # Parameter escaping uses the actual server mode; PREPARE accepts one
        # statement and does not perform that statement's business operation.
        stage = "statement_preparation"
        await cursor.execute("PREPARE praxis_approved FROM %s", (sql,))
        dispatched = True
        await cursor.execute("EXECUTE praxis_approved")
        # Some compatible engines support DML RETURNING. Drain without retaining
        # rows so the completion packet is observed, with bounded client memory.
        if cursor.description:
            while await cursor.fetchmany(200):
                pass
        return {
            "command_status": "completed",
            "affected_rows": cursor.rowcount,
            "returned_rows": False,
        }
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        detail = str(exc)
        if datasource.password:
            detail = detail.replace(datasource.password, "[redacted]")
        if dispatched:
            raise WriteOutcomeUnknownError(detail[:2000]) from exc
        raise WriteNotExecutedError(detail[:2000], stage=stage) from exc
    finally:
        if connection is not None:
            # No graceful-close exception may replace a received success reply.
            if postgres:
                connection.terminate()
            else:
                connection.close()
