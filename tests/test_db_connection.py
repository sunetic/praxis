import asyncio

import pytest

import app.db.connection as connection_module
from app.db.connection import DBConnectionPool, get_db_pool


@pytest.fixture
def anyio_backend():
    return "asyncio"


class DummyCursor:
    def __init__(self):
        self.calls: list[tuple] = []
        self.description = (("col1",),)

    async def execute(self, *args):
        self.calls.append(args)

    async def fetchall(self):
        return [{"col1": 1}]

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class DummyConnection:
    def __init__(self, cursor: DummyCursor):
        self._cursor = cursor

    def cursor(self, _cursor_type=None):
        return self._cursor

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class DummyPool:
    def __init__(self, conn: DummyConnection):
        self._conn = conn
        self.closed = False

    def acquire(self):
        return self._conn

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None


class DummyDatasource:
    host = "127.0.0.1"
    port = 2881
    user = "u"
    password = "p"
    database = "d"


@pytest.mark.anyio
async def test_execute_query_without_params_does_not_pass_empty_sequence(monkeypatch):
    pool = DBConnectionPool()
    cursor = DummyCursor()
    conn = DummyConnection(cursor)
    dummy_pool = DummyPool(conn)

    async def _fake_get_pool(*args, **kwargs):
        return dummy_pool

    monkeypatch.setattr(pool, "_get_pool", _fake_get_pool)

    await pool.execute_query(DummyDatasource(), "SELECT * FROM t WHERE name LIKE '%AUTO%'")

    assert len(cursor.calls) == 1
    assert cursor.calls[0] == ("SELECT * FROM t WHERE name LIKE '%AUTO%'",)


@pytest.mark.anyio
async def test_execute_query_with_params_passes_params(monkeypatch):
    pool = DBConnectionPool()
    cursor = DummyCursor()
    conn = DummyConnection(cursor)
    dummy_pool = DummyPool(conn)

    async def _fake_get_pool(*args, **kwargs):
        return dummy_pool

    monkeypatch.setattr(pool, "_get_pool", _fake_get_pool)

    await pool.execute_query(
        DummyDatasource(),
        "SELECT * FROM t WHERE id = %s",
        params=[1],
    )

    assert len(cursor.calls) == 1
    assert cursor.calls[0] == ("SELECT * FROM t WHERE id = %s", [1])


class FlakyCursor(DummyCursor):
    def __init__(self):
        super().__init__()
        self._failed_once = False

    async def execute(self, *args):
        self.calls.append(args)
        if not self._failed_once:
            self._failed_once = True
            raise Exception(2013, "Lost connection to MySQL server during query ([Errno 60] Operation timed out)")


@pytest.mark.anyio
async def test_execute_query_retries_once_after_connection_lost(monkeypatch):
    pool = DBConnectionPool()
    first_pool = DummyPool(DummyConnection(FlakyCursor()))
    second_cursor = DummyCursor()
    second_pool = DummyPool(DummyConnection(second_cursor))
    created_pools = [first_pool, second_pool]

    async def _fake_create_pool(*args, **kwargs):
        assert created_pools
        return created_pools.pop(0)

    monkeypatch.setattr("app.db.connection.aiomysql.create_pool", _fake_create_pool)

    result = await pool.execute_query(DummyDatasource(), "SELECT 1")

    assert result["row_count"] == 1
    assert result["rows"] == [{"col1": 1}]
    assert first_pool.closed is True
    assert second_cursor.calls == [("SELECT 1",)]


def test_execute_query_rebuilds_pool_when_event_loop_changes(monkeypatch):
    pool = DBConnectionPool()
    created_pools: list[DummyPool] = []

    async def _fake_create_pool(*args, **kwargs):
        new_pool = DummyPool(DummyConnection(DummyCursor()))
        created_pools.append(new_pool)
        return new_pool

    monkeypatch.setattr("app.db.connection.aiomysql.create_pool", _fake_create_pool)

    async def _run_once():
        await pool.execute_query(DummyDatasource(), "SELECT 1")

    asyncio.run(_run_once())
    asyncio.run(_run_once())

    assert len(created_pools) == 2
    assert created_pools[0].closed is True


def test_get_db_pool_returns_distinct_instances_per_group(monkeypatch):
    monkeypatch.setattr(connection_module, "_db_pools", {})

    default_pool = get_db_pool()
    same_default_pool = get_db_pool("default")
    live_pool = get_db_pool("sql_analysis_live")

    assert default_pool is same_default_pool
    assert live_pool is not default_pool
