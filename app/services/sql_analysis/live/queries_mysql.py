from __future__ import annotations

from typing import Any

from app.models import models
from app.schemas import schemas
from app.services.sql_analysis.live.queries import (
    LiveSqlProfileQuery,
    LiveDbNamesQuery,
    LiveCategoryQuery,
    _get_live_db_pool,
)

SYSTEM_DB_NAMES = ("information_schema", "mysql", "performance_schema", "sys")


def _non_system_db_clauses(col: str) -> tuple[list[str], list[Any]]:
    """Build WHERE clauses to exclude MySQL system databases."""
    placeholders = ", ".join(["%s"] * len(SYSTEM_DB_NAMES))
    return [
        f"{col} IS NOT NULL",
        f"TRIM({col}) <> ''",
        f"LOWER({col}) NOT IN ({placeholders})",
    ], list(SYSTEM_DB_NAMES)


def derive_compare_window(
    query: LiveCategoryQuery,
) -> tuple[int | None, int | None]:
    """MySQL does not support compare-window categories."""
    return None, None


async def list_live_category(
    datasource: models.DataSource,
    query: LiveCategoryQuery,
) -> list[dict[str, Any]]:
    """List SQL by category from performance_schema.events_statements_summary_by_digest.

    Only ``top_sql`` and ``slow_sql`` are supported for MySQL.
    """
    if query.category not in {
        schemas.SqlMonitorCategory.TOP_SQL,
        schemas.SqlMonitorCategory.SLOW_SQL,
    }:
        raise ValueError(
            f"MySQL does not support category '{query.category.value}'; "
            "only 'top_sql' and 'slow_sql' are available"
        )

    clauses: list[str] = [
        "SCHEMA_NAME IS NOT NULL",
        "DIGEST_TEXT IS NOT NULL",
        "DIGEST IS NOT NULL",
    ]
    params: list[Any] = []

    if query.db_name:
        clauses.append("SCHEMA_NAME = %s")
        params.append(query.db_name)
    else:
        non_sys_clauses, non_sys_params = _non_system_db_clauses("SCHEMA_NAME")
        clauses.extend(non_sys_clauses)
        params.extend(non_sys_params)

    if query.sql_id:
        clauses.append("DIGEST = %s")
        params.append(query.sql_id)

    if query.keyword:
        clauses.append("DIGEST_TEXT LIKE %s")
        params.append(f"%{query.keyword}%")

    where_sql = " AND ".join(clauses)

    if query.category == schemas.SqlMonitorCategory.TOP_SQL:
        sql = f"""
            /* sql_analysis_live:top_sql_mysql */
            SELECT
              NULL AS ob_tenant_id,
              NULL AS tenant_name,
              NULL AS ob_db_id,
              DIGEST AS sql_id,
              SCHEMA_NAME AS db_name,
              DIGEST_TEXT AS sql_text,
              COUNT_STAR AS executions,
              NULL AS exec_ps,
              SUM_TIMER_WAIT / 1000000 AS sum_elapsed_time_us,
              AVG_TIMER_WAIT / 1000000 AS avg_elapsed_time_us,
              AVG_TIMER_WAIT / 1000000 AS avg_cpu_time_us,
              MAX_TIMER_WAIT / 1000000 AS max_elapsed_time_us
            FROM performance_schema.events_statements_summary_by_digest
            WHERE {where_sql}
            ORDER BY SUM_TIMER_WAIT DESC
            LIMIT %s
        """
        params.append(query.limit)
    else:
        # slow_sql — filter by avg duration threshold
        slow_clauses = [*clauses, "AVG_TIMER_WAIT / 1000000 >= %s"]
        slow_where_sql = " AND ".join(slow_clauses)
        sql = f"""
            /* sql_analysis_live:slow_sql_mysql */
            SELECT
              NULL AS ob_tenant_id,
              NULL AS tenant_name,
              NULL AS ob_db_id,
              DIGEST AS sql_id,
              SCHEMA_NAME AS db_name,
              DIGEST_TEXT AS sql_text,
              COUNT_STAR AS executions,
              NULL AS exec_ps,
              SUM_TIMER_WAIT / 1000000 AS sum_elapsed_time_us,
              AVG_TIMER_WAIT / 1000000 AS avg_elapsed_time_us,
              AVG_TIMER_WAIT / 1000000 AS avg_cpu_time_us,
              MAX_TIMER_WAIT / 1000000 AS max_elapsed_time_us
            FROM performance_schema.events_statements_summary_by_digest
            WHERE {slow_where_sql}
            ORDER BY AVG_TIMER_WAIT DESC
            LIMIT %s
        """
        params.append(query.slow_threshold_us)
        params.append(query.limit)

    result = await _get_live_db_pool().execute_query(
        datasource, sql, role=datasource.tenant_role, params=params,
    )
    items = [dict(row) for row in result.get("rows", [])]
    await _hydrate_real_sql_text(datasource, items)
    return items


async def _hydrate_real_sql_text(
    datasource: models.DataSource,
    items: list[dict[str, Any]],
) -> None:
    """Replace DIGEST_TEXT with real SQL_TEXT from statement history tables when available."""
    digests = [str(item.get("sql_id") or "").strip() for item in items if item.get("sql_id")]
    if not digests:
        return
    unique_digests = list(dict.fromkeys(digests))
    placeholders = ", ".join(["%s"] * len(unique_digests))
    for table in ("events_statements_history", "events_statements_history_long"):
        sql = f"""
            SELECT DIGEST, SQL_TEXT, CURRENT_SCHEMA AS db_name
            FROM performance_schema.{table}
            WHERE DIGEST IN ({placeholders})
              AND SQL_TEXT IS NOT NULL
              AND CURRENT_SCHEMA IS NOT NULL
            ORDER BY TIMER_WAIT DESC
        """
        try:
            result = await _get_live_db_pool().execute_query(
                datasource, sql, role=datasource.tenant_role, params=list(unique_digests),
            )
            for row in result.get("rows", []):
                digest = row.get("DIGEST")
                real_sql = row.get("SQL_TEXT")
                if not digest or not real_sql:
                    continue
                for item in items:
                    if item.get("sql_id") == digest and item.get("_real_sql_text") is None:
                        item["_real_sql_text"] = real_sql
        except Exception:
            continue
    for item in items:
        real = item.pop("_real_sql_text", None)
        if real:
            item["sql_text"] = real


async def list_live_db_names(
    datasource: models.DataSource,
    query: LiveDbNamesQuery,
) -> list[str]:
    """Return distinct non-system schema names from the digest table."""
    non_sys_clauses, non_sys_params = _non_system_db_clauses("SCHEMA_NAME")
    where_sql = " AND ".join(non_sys_clauses)

    sql = f"""
        /* sql_analysis_live:db_names_mysql */
        SELECT DISTINCT SCHEMA_NAME AS db_name
        FROM performance_schema.events_statements_summary_by_digest
        WHERE {where_sql}
        ORDER BY SCHEMA_NAME ASC
        LIMIT 200
    """
    result = await _get_live_db_pool().execute_query(
        datasource, sql, role=datasource.tenant_role, params=non_sys_params,
    )
    rows = result.get("rows", [])
    return [str(row.get("db_name")) for row in rows if row.get("db_name")]


async def get_live_sql_detail(
    datasource: models.DataSource,
    *,
    sql_id: str,
    start_time_us: int,
    end_time_us: int,
    tenant_id: int | None = None,
) -> dict[str, Any] | None:
    """Return aggregate stats for a single DIGEST from performance_schema.

    ``start_time_us``, ``end_time_us``, and ``tenant_id`` are accepted for
    API compatibility but ignored because the digest table holds cumulative
    statistics, not time-windowed data.
    """
    sql = """
        /* sql_analysis_live:sql_detail_mysql */
        SELECT
          NULL AS tenant_id,
          DIGEST AS sql_id,
          SCHEMA_NAME AS db_name,
          NULL AS user_name,
          DIGEST_TEXT AS sql_text,
          COUNT_STAR AS executions,
          AVG_TIMER_WAIT / 1000000 AS avg_elapsed_time_us,
          AVG_TIMER_WAIT / 1000000 AS avg_execute_time_us,
          MAX_TIMER_WAIT / 1000000 AS max_elapsed_time_us,
          UNIX_TIMESTAMP(LAST_SEEN) * 1000000 AS latest_request_time_us,
          NULL AS plan_count
        FROM performance_schema.events_statements_summary_by_digest
        WHERE DIGEST = %s
        LIMIT 1
    """
    result = await _get_live_db_pool().execute_query(
        datasource, sql, role=datasource.tenant_role, params=[sql_id],
    )
    rows = result.get("rows", [])
    if not rows:
        return None
    item = dict(rows[0])
    await _hydrate_real_sql_text(datasource, [item])
    return item


async def get_live_sql_trend(
    datasource: models.DataSource,
    *,
    sql_id: str,
    start_time_us: int,
    end_time_us: int,
    interval_seconds: int = 60,
    tenant_id: int | None = None,
) -> list[dict[str, Any]]:
    """MySQL performance_schema has no per-execution time-series data."""
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
    """MySQL has no plan cache history."""
    return []


def _normalize_digest_text(text: str) -> str:
    """Normalize DIGEST_TEXT spacing to make it executable.
    DIGEST_TEXT adds spaces around tokens: `SUM ( x )` → `SUM(x)`, `col ,` → `col,`
    """
    import re
    s = text.strip()
    s = re.sub(r"\s*\(\s*", "(", s)
    s = re.sub(r"\s*\)\s*", ") ", s)
    s = re.sub(r"\s*,\s*", ", ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


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
    """Attempt EXPLAIN — first try history_long for real SQL, then normalize DIGEST_TEXT."""
    real_sql = await _fetch_real_sql_text(datasource, sql_id, db_name)
    target_sql = real_sql or (_normalize_digest_text(sql_text) if sql_text else None)

    if target_sql and target_sql.strip().lower().startswith("select"):
        try:
            result = await _get_live_db_pool().execute_explain(
                datasource, target_sql, role=datasource.tenant_role, database=db_name,
            )
            rows = [
                {
                    "operator": str(
                        row.get("select_type") or row.get("id") or "EXPLAIN"
                    ),
                    "object_name": row.get("table"),
                    "cost": None,
                    "cardinality": row.get("rows"),
                    "plan_line_id": None,
                    "parent_id": None,
                    "depth": None,
                    "property": row.get("Extra"),
                }
                for row in result.get("rows", [])
            ]
            if rows:
                return "explain_sql", rows
        except Exception:
            pass

    return "unavailable", []


async def _fetch_real_sql_text(
    datasource: models.DataSource,
    digest: str,
    db_name: str | None = None,
) -> str | None:
    """Fetch an actual executable SQL from events_statements_history_long by digest."""
    clauses = [
        "DIGEST = %s",
        "CURRENT_SCHEMA IS NOT NULL",
        "CURRENT_SCHEMA NOT IN (" + ", ".join(["%s"] * len(SYSTEM_DB_NAMES)) + ")",
        "SQL_TEXT IS NOT NULL",
    ]
    params: list[Any] = [digest, *SYSTEM_DB_NAMES]
    if db_name:
        clauses.append("CURRENT_SCHEMA = %s")
        params.append(db_name)
    sql = f"""
        SELECT SQL_TEXT
        FROM performance_schema.events_statements_history_long
        WHERE {" AND ".join(clauses)}
        ORDER BY TIMER_WAIT DESC
        LIMIT 1
    """
    try:
        result = await _get_live_db_pool().execute_query(
            datasource, sql, role=datasource.tenant_role, params=params,
        )
        rows = result.get("rows", [])
        if rows:
            return str(rows[0].get("SQL_TEXT") or "")
    except Exception:
        pass
    return None


async def list_live_sql_profiles(
    datasource: models.DataSource,
    query: LiveSqlProfileQuery,
) -> list[dict[str, Any]]:
    """Return recently-seen SQL digests for the profile view."""
    clauses: list[str] = [
        "DIGEST IS NOT NULL",
        "DIGEST_TEXT IS NOT NULL",
    ]
    params: list[Any] = []

    if query.db_name:
        clauses.append("SCHEMA_NAME = %s")
        params.append(query.db_name)
    else:
        non_sys_clauses, non_sys_params = _non_system_db_clauses("SCHEMA_NAME")
        clauses.extend(non_sys_clauses)
        params.extend(non_sys_params)

    if query.sql_id:
        clauses.append("DIGEST = %s")
        params.append(query.sql_id)

    if query.keyword:
        clauses.append("DIGEST_TEXT LIKE %s")
        params.append(f"%{query.keyword}%")

    where_sql = " AND ".join(clauses)

    sql = f"""
        /* sql_analysis_live:recent_sql_mysql */
        SELECT
          NULL AS tenant_id,
          NULL AS tenant_name,
          SCHEMA_NAME AS db_name,
          NULL AS user_name,
          DIGEST AS sql_id,
          DIGEST_TEXT AS sql_text,
          UNIX_TIMESTAMP(LAST_SEEN) * 1000000 AS latest_request_time_us,
          NULL AS plan_count
        FROM performance_schema.events_statements_summary_by_digest
        WHERE {where_sql}
        ORDER BY LAST_SEEN DESC
        LIMIT %s
    """
    params.append(query.limit)

    result = await _get_live_db_pool().execute_query(
        datasource, sql, role=datasource.tenant_role, params=params,
    )
    return [dict(row) for row in result.get("rows", [])]
