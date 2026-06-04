"""
Database connection management and query execution.

Handles multiple connections (sys tenant and user tenant for OceanBase),
connection pooling, and query execution.
"""

import asyncio
import inspect
from typing import Any, Optional
import aiomysql
from aiomysql import Pool
from opentelemetry import trace

from app.models import models

tracer = trace.get_tracer("app.db.connection")


def _extract_db_operation(sql: str) -> str:
    stripped = sql.strip().lstrip("(")
    first_word = stripped.split(None, 1)[0].upper() if stripped else "UNKNOWN"
    return first_word if first_word in {
        "SELECT", "INSERT", "UPDATE", "DELETE", "EXPLAIN",
        "CREATE", "ALTER", "DROP", "SHOW", "SET", "USE",
    } else "OTHER"


class DBConnectionPool:
    """
    Manages database connections for a data source.
    Supports both sys tenant and user tenant connections.
    """

    def __init__(self):
        self._pools: dict[tuple[str, int, str, str, asyncio.AbstractEventLoop], Pool] = {}

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
    ) -> tuple[str, int, str, str, asyncio.AbstractEventLoop]:
        return (host, int(port), user, database, loop)

    async def _close_pool(self, key: tuple[str, int, str, str, asyncio.AbstractEventLoop]) -> None:
        pool = self._pools.pop(key, None)
        if pool is None:
            return
        try:
            pool.close()
            await pool.wait_closed()
        except Exception:
            # Best-effort cleanup for stale or already-closed pools.
            pass

    async def _close_stale_loop_pools(self, loop: asyncio.AbstractEventLoop) -> None:
        stale_keys = [key for key in self._pools.keys() if key[-1] is not loop]
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
    ) -> Pool:
        """Create or retrieve a connection pool."""
        loop = self._current_loop()
        await self._close_stale_loop_pools(loop)
        key = self._pool_key(host, port, user, database, loop)

        if force_refresh:
            await self._close_pool(key)

        if key not in self._pools:
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
        return {cls._reencode_latin1_value(k): cls._reencode_latin1_value(v) for k, v in row.items()}

    async def _execute_binary_safe(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        sql: str,
        params: Optional[list],
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
                columns = [
                    self._reencode_latin1_value(c[0])
                    for c in cursor.description
                ] if cursor.description else []
                decoded_rows = [self._decode_latin1_row(row) for row in rows]
                return {"rows": decoded_rows, "columns": columns}
        finally:
            conn.close()

    async def execute_query(
        self,
        datasource: models.DataSource,
        sql: str,
        role: str = "user",
        params: Optional[list] = None,
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
        current_loop = self._current_loop()
        pool_key = self._pool_key(host, port, user, database, current_loop)

        db_operation = _extract_db_operation(sql)
        with tracer.start_as_current_span(
            "db.execute_query",
            kind=trace.SpanKind.CLIENT,
            attributes={
                "db.system": "mysql",
                "db.operation": db_operation,
                "db.statement": sql[:500],
                "db.name": database,
                "net.peer.name": host,
                "net.peer.port": port,
            },
        ) as span:
            for attempt in range(2):
                span.set_attribute("db.retry_attempt", attempt)
                pool = await self._get_pool(host, port, user, password, database)
                try:
                    async with pool.acquire() as conn:
                        await self._ping_connection(conn)
                        async with conn.cursor(aiomysql.DictCursor) as cursor:
                            try:
                                if params is None:
                                    await cursor.execute(sql)
                                else:
                                    await cursor.execute(sql, params)
                                rows = await cursor.fetchall()
                                columns = [
                                    self._safe_decode(c[0])
                                    for c in cursor.description
                                ] if cursor.description else []
                            except UnicodeDecodeError:
                                span.add_event("unicode_decode_fallback")
                                fallback = await self._execute_binary_safe(
                                    host, port, user, password, database, sql, params,
                                )
                                rows = fallback["rows"]
                                columns = fallback["columns"]
                            decoded_rows = [self._decode_row(row) for row in rows]
                            cursor_rowcount = getattr(cursor, "rowcount", None)
                            row_count = int(cursor_rowcount) if isinstance(cursor_rowcount, int) and cursor_rowcount >= 0 else len(decoded_rows)

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
        explain_sql = f"EXPLAIN {sql}"
        if database and database != (datasource.database or ""):
            host = datasource.host
            port = datasource.port
            user = datasource.user or ""
            password = datasource.password or ""
            with tracer.start_as_current_span(
                "db.execute_explain",
                attributes={"db.system": "mysql", "db.statement": sql[:500], "db.name": database},
            ):
                conn = await aiomysql.connect(
                    host=host, port=port, user=user, password=password,
                    db=database, autocommit=True,
                )
                try:
                    async with conn.cursor(aiomysql.DictCursor) as cursor:
                        await cursor.execute(explain_sql)
                        rows = list(await cursor.fetchall())
                        columns = [c[0] for c in cursor.description] if cursor.description else []
                        return {"columns": columns, "rows": [self._decode_row(r) for r in rows], "row_count": len(rows)}
                finally:
                    conn.close()
        with tracer.start_as_current_span(
            "db.execute_explain",
            attributes={"db.system": "mysql", "db.statement": sql[:500]},
        ):
            return await self.execute_query(datasource, explain_sql, role)

    async def test_connection(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
    ) -> tuple[bool, str]:
        """
        Test database connection.

        Returns:
            (success: bool, message: str)
        """
        try:
            pool = await self._get_pool(host, port, user, password, database)
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
