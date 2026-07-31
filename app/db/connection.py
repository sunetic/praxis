"""
Database connection management and query execution.

Handles multiple connections (sys tenant and user tenant for OceanBase),
connection pooling, and query execution.
"""

import asyncio
import inspect
import re
from typing import Any

import aiomysql
import asyncpg
from opentelemetry import trace

from app.models import models

PoolType = aiomysql.Pool | asyncpg.Pool

tracer = trace.get_tracer("app.db.connection")


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
            "USE",
        }
        else "OTHER"
    )


class DBConnectionPool:
    """
    Manages database connections for a data source.
    Supports both sys tenant and user tenant connections.
    """

    def __init__(self):
        self._pools: dict[tuple, PoolType] = {}

    @staticmethod
    def _current_loop() -> asyncio.AbstractEventLoop:
        return asyncio.get_running_loop()

    @staticmethod
    def _pool_key(
        host: str,
        port: int,
        user: str,
        database: str,
        loop: asyncio.AbstractEventLoop,
        db_type: str = "mysql",
    ) -> tuple:
        return (host, int(port), user, database, loop, db_type)

    async def _close_pool(self, key: tuple) -> None:
        pool = self._pools.pop(key, None)
        if pool is None:
            return
        try:
            if isinstance(pool, asyncpg.Pool):
                await pool.close()
            else:
                pool.close()
                await pool.wait_closed()
        except Exception:
            pass

    async def _close_stale_loop_pools(self, loop: asyncio.AbstractEventLoop) -> None:
        stale_keys = [key for key in self._pools.keys() if key[4] is not loop]
        for key in stale_keys:
            await self._close_pool(key)

    async def _ping_connection(self, conn: Any) -> None:
        ping = getattr(conn, "ping", None)
        if ping is None:
            return
        try:
            result = ping(reconnect=True)
        except TypeError:
            result = ping()
        if inspect.isawaitable(result):
            await result

    @staticmethod
    def _is_connection_lost_error(exc: Exception) -> bool:
        args = getattr(exc, "args", ())
        if isinstance(args, tuple) and args and isinstance(args[0], int):
            if args[0] in {2006, 2013, 2055}:
                return True
        normalized = str(exc).lower()
        return (
            "lost connection" in normalized
            or "server has gone away" in normalized
            or "operation timed out" in normalized
            or "connection reset by peer" in normalized
        )

    async def _get_pool(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        pool_size: int = 5,
        force_refresh: bool = False,
        db_type: str = "mysql",
    ) -> PoolType:
        """Create or retrieve a connection pool."""
        loop = self._current_loop()
        await self._close_stale_loop_pools(loop)
        key = self._pool_key(host, port, user, database, loop, db_type)

        if force_refresh:
            await self._close_pool(key)

        if key not in self._pools:
            if db_type == "postgresql":
                self._pools[key] = await asyncpg.create_pool(
                    host=host,
                    port=port,
                    user=user,
                    password=password,
                    database=database,
                    min_size=1,
                    max_size=pool_size,
                )
            else:
                self._pools[key] = await aiomysql.create_pool(
                    host=host,
                    port=port,
                    user=user,
                    password=password,
                    db=database,
                    minsize=1,
                    maxsize=pool_size,
                    autocommit=True,
                )

        return self._pools[key]

    @staticmethod
    def _safe_decode(value: Any) -> Any:
        """Decode bytes to str, replacing invalid UTF-8 sequences."""
        if isinstance(value, (bytes, bytearray)):
            return value.decode("utf-8", errors="replace")
        return value

    @staticmethod
    def _reencode_latin1_value(value: Any) -> Any:
        """Re-encode a latin1-decoded str back to bytes, then decode as UTF-8 with replacement."""
        if isinstance(value, str):
            try:
                return value.encode("latin1").decode("utf-8", errors="replace")
            except (UnicodeEncodeError, UnicodeDecodeError):
                return value
        if isinstance(value, (bytes, bytearray)):
            return value.decode("utf-8", errors="replace")
        return value

    @classmethod
    def _decode_row(cls, row: dict) -> dict:
        return {cls._safe_decode(k): cls._safe_decode(v) for k, v in row.items()}

    @classmethod
    def _decode_latin1_row(cls, row: dict) -> dict:
        return {
            cls._reencode_latin1_value(k): cls._reencode_latin1_value(v) for k, v in row.items()
        }

    async def _execute_binary_safe(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        sql: str,
        params: list | None,
    ) -> dict[str, Any]:
        """Re-execute a query with use_unicode=False to handle invalid UTF-8 data."""
        conn = await aiomysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            db=database,
            charset="latin1",
            autocommit=True,
        )
        conn._encoding = "latin-1"
        try:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                if params is None:
                    await cursor.execute(sql)
                else:
                    await cursor.execute(sql, params)
                rows = list(await cursor.fetchall())
                columns = (
                    [self._reencode_latin1_value(c[0]) for c in cursor.description]
                    if cursor.description
                    else []
                )
                decoded_rows = [self._decode_latin1_row(row) for row in rows]
                return {"rows": decoded_rows, "columns": columns}
        finally:
            conn.close()

    @staticmethod
    def _mysql_to_pg_placeholders(sql: str) -> tuple[str, bool]:
        """Convert %s placeholders to $1, $2, ... for asyncpg."""
        counter = 0

        def _replace(match: re.Match) -> str:
            nonlocal counter
            counter += 1
            return f"${counter}"

        converted = re.sub(r"%s", _replace, sql)
        return converted, counter > 0

    async def _execute_query_pg(
        self,
        pool: asyncpg.Pool,
        sql: str,
        params: list | None,
        span: Any,
    ) -> dict[str, Any]:
        """Execute a query using asyncpg."""
        pg_sql, _ = self._mysql_to_pg_placeholders(sql)
        async with pool.acquire() as conn:
            if params:
                rows = await conn.fetch(pg_sql, *params)
            else:
                rows = await conn.fetch(pg_sql)
            if rows:
                columns = list(rows[0].keys())
                decoded_rows = [dict(r) for r in rows]
            else:
                columns = []
                decoded_rows = []
            row_count = len(decoded_rows)
            span.set_attribute("db.row_count", row_count)
            return {
                "columns": columns,
                "rows": decoded_rows,
                "row_count": row_count,
            }

    async def execute_query(
        self,
        datasource: models.DataSource,
        sql: str,
        role: str = "user",
        params: list | None = None,
    ) -> dict[str, Any]:
        """
        Execute a SQL query.

        Args:
            datasource: DataSource model instance
            sql: SQL query to execute
            role: "sys" for sys tenant, "user"/"tenant" for user tenant
            params: Query parameters

        Returns:
            dict with keys: columns, rows, row_count
        """
        host = datasource.host
        port = datasource.port
        user = datasource.user or ""
        password = datasource.password or ""
        database = datasource.database or ""
        db_type = getattr(datasource, "db_type", "mysql") or "mysql"
        current_loop = self._current_loop()
        pool_key = self._pool_key(host, port, user, database, current_loop, db_type)

        db_operation = _extract_db_operation(sql)
        with tracer.start_as_current_span(
            "db.execute_query",
            kind=trace.SpanKind.CLIENT,
            attributes={
                "db.system": db_type,
                "db.operation": db_operation,
                "db.statement": sql[:500],
                "db.name": database,
                "net.peer.name": host,
                "net.peer.port": port,
            },
        ) as span:
            for attempt in range(2):
                span.set_attribute("db.retry_attempt", attempt)
                pool = await self._get_pool(host, port, user, password, database, db_type=db_type)
                try:
                    if db_type == "postgresql":
                        return await self._execute_query_pg(pool, sql, params, span)

                    async with pool.acquire() as conn:
                        await self._ping_connection(conn)
                        async with conn.cursor(aiomysql.DictCursor) as cursor:
                            try:
                                if params is None:
                                    await cursor.execute(sql)
                                else:
                                    await cursor.execute(sql, params)
                                rows = await cursor.fetchall()
                                columns = (
                                    [self._safe_decode(c[0]) for c in cursor.description]
                                    if cursor.description
                                    else []
                                )
                            except UnicodeDecodeError:
                                span.add_event("unicode_decode_fallback")
                                fallback = await self._execute_binary_safe(
                                    host,
                                    port,
                                    user,
                                    password,
                                    database,
                                    sql,
                                    params,
                                )
                                rows = fallback["rows"]
                                columns = fallback["columns"]
                            decoded_rows = [self._decode_row(row) for row in rows]
                            cursor_rowcount = getattr(cursor, "rowcount", None)
                            row_count = (
                                int(cursor_rowcount)
                                if isinstance(cursor_rowcount, int) and cursor_rowcount >= 0
                                else len(decoded_rows)
                            )

                            span.set_attribute("db.row_count", row_count)
                            return {
                                "columns": columns,
                                "rows": decoded_rows,
                                "row_count": row_count,
                            }
                except Exception as e:
                    if attempt == 0 and self._is_connection_lost_error(e):
                        span.add_event("connection_lost_retry", {"error": str(e)})
                        await self._close_pool(pool_key)
                        continue
                    span.record_exception(e)
                    span.set_status(trace.StatusCode.ERROR, str(e))
                    raise

    async def execute_explain(
        self,
        datasource: models.DataSource,
        sql: str,
        role: str = "user",
        database: str | None = None,
    ) -> dict[str, Any]:
        """
        Execute EXPLAIN for a SQL query.

        Args:
            database: override the datasource's default database for this EXPLAIN
        Returns:
            dict with execution plan details
        """
        db_type = getattr(datasource, "db_type", "mysql") or "mysql"
        explain_sql = f"EXPLAIN {sql}"
        if database and database != (datasource.database or ""):
            host = datasource.host
            port = datasource.port
            user = datasource.user or ""
            password = datasource.password or ""
            with tracer.start_as_current_span(
                "db.execute_explain",
                attributes={"db.system": db_type, "db.statement": sql[:500], "db.name": database},
            ):
                if db_type == "postgresql":
                    conn = await asyncpg.connect(
                        host=host,
                        port=port,
                        user=user,
                        password=password,
                        database=database,
                    )
                    try:
                        rows = await conn.fetch(explain_sql)
                        columns = list(rows[0].keys()) if rows else []
                        return {
                            "columns": columns,
                            "rows": [dict(r) for r in rows],
                            "row_count": len(rows),
                        }
                    finally:
                        await conn.close()
                else:
                    conn = await aiomysql.connect(
                        host=host,
                        port=port,
                        user=user,
                        password=password,
                        db=database,
                        autocommit=True,
                    )
                    try:
                        async with conn.cursor(aiomysql.DictCursor) as cursor:
                            await cursor.execute(explain_sql)
                            rows = list(await cursor.fetchall())
                            columns = (
                                [c[0] for c in cursor.description] if cursor.description else []
                            )
                            return {
                                "columns": columns,
                                "rows": [self._decode_row(r) for r in rows],
                                "row_count": len(rows),
                            }
                    finally:
                        conn.close()
        with tracer.start_as_current_span(
            "db.execute_explain",
            attributes={"db.system": db_type, "db.statement": sql[:500]},
        ):
            return await self.execute_query(datasource, explain_sql, role)

    async def test_connection(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        db_type: str = "mysql",
    ) -> tuple[bool, str]:
        """
        Test database connection.

        Returns:
            (success: bool, message: str)
        """
        try:
            pool = await self._get_pool(host, port, user, password, database, db_type=db_type)
            if db_type == "postgresql":
                async with pool.acquire() as conn:
                    await conn.fetchval("SELECT 1")
            else:
                async with pool.acquire() as conn:
                    async with conn.cursor() as cursor:
                        await cursor.execute("SELECT 1")
                        await cursor.fetchone()
            return (True, "Connection successful")
        except Exception as e:
            return (False, str(e))

    async def close_all(self) -> None:
        """Close all connection pools."""
        for key in list(self._pools.keys()):
            await self._close_pool(key)

    def __del__(self):
        """Cleanup on garbage collection."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self.close_all())
            else:
                loop.run_until_complete(self.close_all())
        except Exception:
            pass


# Global connection pool instance
_DB_POOL_GROUP_DEFAULT = "default"
_db_pools: dict[str, DBConnectionPool] = {}


def get_db_pool(group: str = _DB_POOL_GROUP_DEFAULT) -> DBConnectionPool:
    """Get or create a named global connection pool."""
    normalized_group = str(group or _DB_POOL_GROUP_DEFAULT).strip() or _DB_POOL_GROUP_DEFAULT
    pool = _db_pools.get(normalized_group)
    if pool is None:
        pool = DBConnectionPool()
        _db_pools[normalized_group] = pool
    return pool
