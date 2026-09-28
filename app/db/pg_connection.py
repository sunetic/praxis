"""
PostgreSQL async connection pool via asyncpg.
"""

from __future__ import annotations

import asyncio
from typing import Any

import asyncpg
from opentelemetry import trace

tracer = trace.get_tracer("app.db.pg_connection")


def _extract_db_operation(sql: str) -> str:
    stripped = sql.strip().lstrip("(")
    first_word = stripped.split(None, 1)[0].upper() if stripped else "UNKNOWN"
    return (
        first_word
        if first_word
        in {
            "SELECT",
            "INSERT",
            "UPDATE",
            "DELETE",
            "EXPLAIN",
            "CREATE",
            "ALTER",
            "DROP",
            "SHOW",
            "SET",
        }
        else "OTHER"
    )


def _convert_params(sql: str, params: list[Any]) -> tuple[str, list[Any]]:
    """Convert %s placeholders to $1, $2, ... for asyncpg."""
    result: list[str] = []
    idx = 0
    i = 0
    while i < len(sql):
        if sql[i] == "%" and i + 1 < len(sql) and sql[i + 1] == "s":
            idx += 1
            result.append(f"${idx}")
            i += 2
        else:
            result.append(sql[i])
            i += 1
    return "".join(result), params


class PgConnectionPool:
    def __init__(self) -> None:
        self._pools: dict[tuple[str, int, str, str, asyncio.AbstractEventLoop], asyncpg.Pool] = {}

    @staticmethod
    def _current_loop() -> asyncio.AbstractEventLoop:
        return asyncio.get_running_loop()

    def _pool_key(
        self, host: str, port: int, user: str, database: str
    ) -> tuple[str, int, str, str, asyncio.AbstractEventLoop]:
        return (host, int(port), user, database, self._current_loop())

    async def _get_pool(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
    ) -> asyncpg.Pool:
        key = self._pool_key(host, port, user, database)
        if key not in self._pools:
            self._pools[key] = await asyncpg.create_pool(
                host=host,
                port=port,
                user=user,
                password=password,
                database=database,
                min_size=1,
                max_size=5,
            )
        return self._pools[key]

    async def execute_read_query(
        self,
        datasource: Any,
        sql: str,
        *,
        params: list[Any] | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Bounded native read-only transaction; never retry a submitted operation."""
        pool = await self._get_pool(
            datasource.host,
            datasource.port,
            datasource.user or "",
            datasource.password or "",
            datasource.database or "",
        )
        async with pool.acquire() as conn:
            async with conn.transaction(readonly=True):
                converted_sql, converted_params = _convert_params(sql, params or [])
                statement = await conn.prepare(converted_sql)
                columns = [attribute.name for attribute in statement.get_attributes()]
                cursor = await statement.cursor(*converted_params)
                records = await cursor.fetch(limit + 1)
                return {
                    "columns": columns,
                    "rows": [dict(record) for record in records[:limit]],
                    "returned_rows": min(len(records), limit),
                    "truncated": len(records) > limit,
                    "total_rows": None if len(records) > limit else len(records),
                }

    async def execute_query(
        self,
        datasource: Any,
        sql: str,
        role: str = "user",
        params: list[Any] | None = None,
    ) -> dict[str, Any]:
        host = datasource.host
        port = datasource.port
        user = datasource.user or ""
        password = datasource.password or ""
        database = datasource.database or ""

        db_operation = _extract_db_operation(sql)
        with tracer.start_as_current_span(
            "db.execute_query",
            kind=trace.SpanKind.CLIENT,
            attributes={
                "db.system": "postgresql",
                "db.operation": db_operation,
                "db.statement": sql[:500],
                "db.name": database,
                "net.peer.name": host,
                "net.peer.port": port,
            },
        ) as span:
            pool = await self._get_pool(host, port, user, password, database)
            try:
                async with pool.acquire() as conn:
                    converted_sql, converted_params = _convert_params(sql, params or [])
                    records = await conn.fetch(converted_sql, *converted_params)
                    if not records:
                        return {"columns": [], "rows": [], "row_count": 0}
                    columns = list(records[0].keys())
                    rows = [dict(r) for r in records]
                    span.set_attribute("db.row_count", len(rows))
                    return {"columns": columns, "rows": rows, "row_count": len(rows)}
            except Exception as e:
                span.record_exception(e)
                span.set_status(trace.StatusCode.ERROR, str(e))
                raise

    async def execute_explain(
        self,
        datasource: Any,
        sql: str,
        role: str = "user",
        *,
        analyze: bool = False,
        timeout_seconds: int = 10,
    ) -> dict[str, Any]:
        _ = role
        timeout_ms = max(1, min(int(timeout_seconds), 60)) * 1000
        prefix = "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)" if analyze else "EXPLAIN (FORMAT JSON)"
        pool = await self._get_pool(
            datasource.host,
            datasource.port,
            datasource.user or "",
            datasource.password or "",
            datasource.database or "",
        )
        async with pool.acquire() as conn:
            async with conn.transaction(readonly=True):
                await conn.execute(f"SET LOCAL statement_timeout = {timeout_ms}")
                records = await conn.fetch(f"{prefix} {sql}")
                columns = list(records[0].keys()) if records else []
                return {
                    "columns": columns,
                    "rows": [dict(record) for record in records],
                    "returned_rows": len(records),
                    "truncated": False,
                    "total_rows": len(records),
                    "analyze": analyze,
                    "timeout_seconds": timeout_ms // 1000,
                }

    async def test_connection(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
    ) -> tuple[bool, str]:
        try:
            pool = await self._get_pool(host, port, user, password, database)
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return (True, "Connection successful")
        except Exception as e:
            return (False, str(e))

    async def close_all(self) -> None:
        for pool in list(self._pools.values()):
            try:
                await pool.close()
            except Exception:
                pass
        self._pools.clear()
