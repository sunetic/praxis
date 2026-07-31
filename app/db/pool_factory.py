"""
Returns the appropriate connection pool for a given datasource based on db_type.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    from app.db.connection import DBConnectionPool
    from app.db.pg_connection import PgConnectionPool
    from app.models.models import DataSource

AnyPool = Union["DBConnectionPool", "PgConnectionPool"]

_PG_TYPES = {"postgresql", "postgres"}


def get_pool_for_datasource(datasource: DataSource) -> AnyPool:
    db_type = (datasource.db_type or "").strip().lower()
    if db_type in _PG_TYPES:
        return _get_pg_pool()
    from app.db.connection import get_db_pool

    return get_db_pool()


_pg_pool: PgConnectionPool | None = None


def _get_pg_pool() -> PgConnectionPool:
    global _pg_pool
    if _pg_pool is None:
        from app.db.pg_connection import PgConnectionPool

        _pg_pool = PgConnectionPool()
    return _pg_pool
