from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType
from typing import Any

from app.models import models
from app.schemas import schemas

SQL_ANALYSIS_LIVE_POOL_GROUP = "sql_analysis_live"


@dataclass(frozen=True)
class LiveSqlProfileQuery:
    start_time_us: int
    end_time_us: int
    limit: int = 20
    tenant_id: int | None = None
    tenant_name: str | None = None
    db_name: str | None = None
    sql_id: str | None = None
    keyword: str | None = None


@dataclass(frozen=True)
class LiveDbNamesQuery:
    start_time_us: int
    end_time_us: int
    tenant_id: int | None = None
    tenant_name: str | None = None


@dataclass(frozen=True)
class LiveCategoryQuery:
    category: schemas.SqlMonitorCategory
    start_time_us: int
    end_time_us: int
    limit: int = 20
    compare_start_time_us: int | None = None
    compare_end_time_us: int | None = None
    tenant_id: int | None = None
    tenant_name: str | None = None
    db_name: str | None = None
    sql_id: str | None = None
    keyword: str | None = None
    slow_threshold_us: int = 1_000_000


def _get_live_db_pool():
    from app.db.connection import get_db_pool

    try:
        return get_db_pool(group=SQL_ANALYSIS_LIVE_POOL_GROUP)
    except TypeError:
        return get_db_pool()


def _get_driver(datasource: models.DataSource) -> ModuleType:
    import importlib

    _driver_modules: dict[str, str] = {
        "mysql": "app.services.sql_analysis.live.queries_mysql",
        "postgresql": "app.services.sql_analysis.live.queries_pg",
    }
    module_path = _driver_modules.get(datasource.db_type)
    if module_path is None:
        raise ValueError(f"SQL analysis is not supported for db_type '{datasource.db_type}'")
    return importlib.import_module(module_path)


def derive_compare_window(query: LiveCategoryQuery) -> tuple[int | None, int | None]:
    return None, None


def derive_live_compare_window(query: LiveCategoryQuery) -> tuple[int | None, int | None]:
    return None, None


async def list_live_sql_profiles(
    datasource: models.DataSource,
    query: LiveSqlProfileQuery,
) -> list[dict[str, Any]]:
    return await _get_driver(datasource).list_live_sql_profiles(datasource, query)


async def list_live_db_names(
    datasource: models.DataSource,
    query: LiveDbNamesQuery,
) -> list[str]:
    return await _get_driver(datasource).list_live_db_names(datasource, query)


async def list_live_category(
    datasource: models.DataSource,
    query: LiveCategoryQuery,
) -> list[dict[str, Any]]:
    return await _get_driver(datasource).list_live_category(datasource, query)


async def get_live_sql_detail(
    datasource: models.DataSource,
    *,
    sql_id: str,
    start_time_us: int,
    end_time_us: int,
    tenant_id: int | None = None,
) -> dict[str, Any] | None:
    return await _get_driver(datasource).get_live_sql_detail(
        datasource,
        sql_id=sql_id,
        start_time_us=start_time_us,
        end_time_us=end_time_us,
        tenant_id=tenant_id,
    )


async def get_live_sql_trend(
    datasource: models.DataSource,
    *,
    sql_id: str,
    start_time_us: int,
    end_time_us: int,
    interval_seconds: int = 60,
    tenant_id: int | None = None,
) -> list[dict[str, Any]]:
    return await _get_driver(datasource).get_live_sql_trend(
        datasource,
        sql_id=sql_id,
        start_time_us=start_time_us,
        end_time_us=end_time_us,
        interval_seconds=interval_seconds,
        tenant_id=tenant_id,
    )


async def list_live_plan_history(
    datasource: models.DataSource,
    *,
    sql_id: str,
    start_time_us: int | None = None,
    end_time_us: int | None = None,
    tenant_id: int | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    return await _get_driver(datasource).list_live_plan_history(
        datasource,
        sql_id=sql_id,
        start_time_us=start_time_us,
        end_time_us=end_time_us,
        tenant_id=tenant_id,
        limit=limit,
    )


async def get_live_plan_explain(
    datasource: models.DataSource,
    *,
    sql_id: str,
    plan_id: int | None = None,
    plan_hash: int | None = None,
    tenant_id: int | None = None,
    sql_text: str | None = None,
    db_name: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    return await _get_driver(datasource).get_live_plan_explain(
        datasource,
        sql_id=sql_id,
        plan_id=plan_id,
        plan_hash=plan_hash,
        tenant_id=tenant_id,
        sql_text=sql_text,
        db_name=db_name,
    )
