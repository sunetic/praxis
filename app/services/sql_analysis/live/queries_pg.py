from __future__ import annotations

from typing import Any

from app.models import models
from app.schemas import schemas
from app.services.sql_analysis.live.queries import (
    LiveCategoryQuery,
    LiveDbNamesQuery,
    LiveSqlProfileQuery,
    _get_live_db_pool,
)

SYSTEM_DB_NAMES = ("information_schema", "pg_catalog")


def _non_system_schema_clauses(alias: str) -> tuple[list[str], list[Any]]:
    placeholders = ", ".join(["%s"] * len(SYSTEM_DB_NAMES))
    return [
        f"{alias}.dbname IS NOT NULL",
        f"TRIM({alias}.dbname) <> ''",
        f"LOWER({alias}.dbname) NOT IN ({placeholders})",
    ], list(SYSTEM_DB_NAMES)


def derive_compare_window(query: LiveCategoryQuery) -> tuple[int | None, int | None]:
    return None, None


async def list_live_category(
    datasource: models.DataSource,
    query: LiveCategoryQuery,
) -> list[dict[str, Any]]:
    if query.end_time_us <= query.start_time_us:
        raise ValueError("end_time_us must be greater than start_time_us")

    if query.category not in {
        schemas.SqlMonitorCategory.TOP_SQL,
        schemas.SqlMonitorCategory.SLOW_SQL,
    }:
        raise ValueError(f"Category '{query.category.value}' is not supported for PostgreSQL")

    clauses: list[str] = [
        "s.query IS NOT NULL",
        "s.queryid IS NOT NULL",
    ]
    params: list[Any] = []

    if query.db_name:
        clauses.append("d.datname = %s")
        params.append(query.db_name)
    else:
        non_sys_clauses, non_sys_params = _non_system_schema_clauses("d")
        clauses.extend(non_sys_clauses)
        params.extend(non_sys_params)

    if query.keyword:
        clauses.append("s.query LIKE %s")
        params.append(f"%{query.keyword}%")

    where_sql = " AND ".join(clauses)

    if query.category == schemas.SqlMonitorCategory.SLOW_SQL:
        order_clause = "avg_elapsed_time_us DESC"
        having_clause = f"HAVING s.mean_exec_time * 1000 >= {int(query.slow_threshold_us)}"
    else:
        order_clause = "sum_elapsed_time_us DESC"
        having_clause = ""

    sql = f"""
        SELECT
          NULL AS ob_tenant_id,
          NULL AS tenant_name,
          NULL AS ob_db_id,
          CAST(s.queryid AS TEXT) AS sql_id,
          d.datname AS db_name,
          s.query AS sql_text,
          s.calls AS executions,
          NULL AS exec_ps,
          ROUND(s.total_exec_time * 1000)::bigint AS sum_elapsed_time_us,
          ROUND(s.mean_exec_time * 1000)::bigint AS avg_elapsed_time_us,
          ROUND(s.mean_exec_time * 1000)::bigint AS avg_cpu_time_us,
          ROUND(s.max_exec_time * 1000)::bigint AS max_elapsed_time_us
        FROM pg_stat_statements s
        JOIN pg_database d ON d.oid = s.dbid
        WHERE {where_sql}
        {having_clause}
        ORDER BY {order_clause}
        LIMIT %s
    """
    params.append(query.limit)

    result = await _get_live_db_pool().execute_query(
        datasource,
        sql,
        role=datasource.tenant_role,
        params=params,
    )
    return [dict(row) for row in result.get("rows", [])]


async def list_live_db_names(
    datasource: models.DataSource,
    query: LiveDbNamesQuery,
) -> list[str]:
    if query.end_time_us <= query.start_time_us:
        raise ValueError("end_time_us must be greater than start_time_us")

    non_sys_clauses, non_sys_params = _non_system_schema_clauses("d")
    where_sql = " AND ".join(non_sys_clauses)
    sql = f"""
        SELECT DISTINCT d.datname AS db_name
        FROM pg_stat_statements s
        JOIN pg_database d ON d.oid = s.dbid
        WHERE {where_sql}
        ORDER BY d.datname ASC
        LIMIT 200
    """
    result = await _get_live_db_pool().execute_query(
        datasource,
        sql,
        role=datasource.tenant_role,
        params=non_sys_params,
    )
    return [str(row.get("db_name")) for row in result.get("rows", []) if row.get("db_name")]


async def get_live_sql_detail(
    datasource: models.DataSource,
    *,
    sql_id: str,
    start_time_us: int,
    end_time_us: int,
    tenant_id: int | None = None,
) -> dict[str, Any] | None:
    sql = """
        SELECT
          NULL AS tenant_id,
          CAST(s.queryid AS TEXT) AS sql_id,
          d.datname AS db_name,
          r.rolname AS user_name,
          s.query AS sql_text,
          s.calls AS executions,
          ROUND(s.mean_exec_time * 1000)::bigint AS avg_elapsed_time_us,
          ROUND(s.mean_exec_time * 1000)::bigint AS avg_execute_time_us,
          ROUND(s.max_exec_time * 1000)::bigint AS max_elapsed_time_us,
          NULL AS latest_request_time_us,
          NULL AS plan_count
        FROM pg_stat_statements s
        JOIN pg_database d ON d.oid = s.dbid
        LEFT JOIN pg_roles r ON r.oid = s.userid
        WHERE CAST(s.queryid AS TEXT) = %s
        LIMIT 1
    """
    result = await _get_live_db_pool().execute_query(
        datasource,
        sql,
        role=datasource.tenant_role,
        params=[sql_id],
    )
    rows = result.get("rows", [])
    return dict(rows[0]) if rows else None


async def get_live_sql_trend(
    datasource: models.DataSource,
    *,
    sql_id: str,
    start_time_us: int,
    end_time_us: int,
    interval_seconds: int = 60,
    tenant_id: int | None = None,
) -> list[dict[str, Any]]:
    return []


async def list_live_plan_history(
    datasource: models.DataSource,
    *,
    sql_id: str,
    start_time_us: int | None = None,
    end_time_us: int | None = None,
    tenant_id: int | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    return []


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
    if sql_text and sql_text.strip().lower().startswith("select"):
        try:
            result = await _get_live_db_pool().execute_explain(
                datasource,
                sql_text,
                role=datasource.tenant_role,
                database=db_name,
            )
            rows = [
                {
                    "operator": str(row.get("QUERY PLAN") or "EXPLAIN"),
                    "object_name": None,
                    "cost": None,
                    "cardinality": None,
                    "plan_line_id": None,
                    "parent_id": None,
                    "depth": None,
                    "property": None,
                }
                for row in result.get("rows", [])
            ]
            if rows:
                return "explain_sql", rows
        except Exception:
            pass

    return "unavailable", []


async def list_live_sql_profiles(
    datasource: models.DataSource,
    query: LiveSqlProfileQuery,
) -> list[dict[str, Any]]:
    if query.end_time_us <= query.start_time_us:
        raise ValueError("end_time_us must be greater than start_time_us")

    clauses: list[str] = [
        "s.queryid IS NOT NULL",
        "s.query IS NOT NULL",
    ]
    params: list[Any] = []

    if query.db_name:
        clauses.append("d.datname = %s")
        params.append(query.db_name)
    else:
        non_sys_clauses, non_sys_params = _non_system_schema_clauses("d")
        clauses.extend(non_sys_clauses)
        params.extend(non_sys_params)

    if query.sql_id:
        clauses.append("CAST(s.queryid AS TEXT) = %s")
        params.append(query.sql_id)
    if query.keyword:
        clauses.append("s.query LIKE %s")
        params.append(f"%{query.keyword}%")

    where_sql = " AND ".join(clauses)
    sql = f"""
        SELECT
          NULL AS tenant_id,
          NULL AS tenant_name,
          d.datname AS db_name,
          r.rolname AS user_name,
          CAST(s.queryid AS TEXT) AS sql_id,
          s.query AS sql_text,
          NULL AS latest_request_time_us,
          NULL AS plan_count
        FROM pg_stat_statements s
        JOIN pg_database d ON d.oid = s.dbid
        LEFT JOIN pg_roles r ON r.oid = s.userid
        WHERE {where_sql}
        ORDER BY s.total_exec_time DESC
        LIMIT %s
    """
    params.append(query.limit)

    result = await _get_live_db_pool().execute_query(
        datasource,
        sql,
        role=datasource.tenant_role,
        params=params,
    )
    return [dict(row) for row in result.get("rows", [])]
