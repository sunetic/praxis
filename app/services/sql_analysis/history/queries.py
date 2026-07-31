from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

from app.core.config import get_settings
from app.models import models
from app.schemas import schemas


def _get_monitor_datasource() -> models.DataSource:
    """Construct a virtual DataSource from config for connecting to monitor_db."""
    s = get_settings()
    ds = models.DataSource(
        id=0,
        name="__monitor_db__",
        host=s.monitor_db_host,
        port=s.monitor_db_port,
        db_type="oceanbase",
        cluster_key="__config__",
        tenant_role="user",
        user=s.monitor_db_user,
        password=s.monitor_db_password,
        database=s.monitor_db_database,
        status="active",
    )
    return ds


SQLTEXT_LOOKBACK_WINDOW_US = 30 * 24 * 60 * 60 * 1_000_000
PLAN_LOOKBACK_WINDOW_US = 30 * 24 * 60 * 60 * 1_000_000


@dataclass(frozen=True)
class MonitorCategoryQuery:
    category: schemas.SqlMonitorCategory
    start_time_us: int
    end_time_us: int
    limit: int = 20
    compare_start_time_us: int | None = None
    compare_end_time_us: int | None = None
    datasource_id: int | None = None
    db_name: str | None = None
    sql_id: str | None = None
    keyword: str | None = None
    slow_threshold_us: int = 1_000_000
    cursor: str | None = None


def _derive_compare_window(query: MonitorCategoryQuery) -> tuple[int | None, int | None]:
    if query.compare_start_time_us is not None and query.compare_end_time_us is not None:
        return query.compare_start_time_us, query.compare_end_time_us
    if query.category not in {
        schemas.SqlMonitorCategory.NEW_SQL,
        schemas.SqlMonitorCategory.REGRESSED_SQL,
    }:
        return None, None
    window = query.end_time_us - query.start_time_us
    if window <= 0:
        return None, None
    return query.start_time_us - window, query.start_time_us


def derive_compare_window(query: MonitorCategoryQuery) -> tuple[int | None, int | None]:
    return _derive_compare_window(query)


def _lookback_start_us(end_time_us: int, window_us: int) -> int:
    return max(0, end_time_us - window_us)


def _decode_cursor(cursor: str | None) -> dict | None:
    if not cursor:
        return None
    try:
        return json.loads(base64.b64decode(cursor.encode()).decode())
    except Exception:
        return None


def _encode_cursor(v: Any, sql_id: str) -> str:
    # Decimal values from DB are not JSON-serializable; convert to float
    if hasattr(v, "__float__"):
        v = float(v)
    return base64.b64encode(json.dumps({"v": v, "id": sql_id}).encode()).decode()


# ---------------------------------------------------------------------------
# Common filter builders (praxis collector schema)
# ---------------------------------------------------------------------------


def _build_common_filters(alias: str, query: MonitorCategoryQuery) -> tuple[list[str], list[Any]]:
    """Build WHERE clauses for datasource_id / sql_id."""
    clauses: list[str] = []
    params: list[Any] = []
    if query.datasource_id is not None:
        clauses.append(f"{alias}.datasource_id = %s")
        params.append(query.datasource_id)
    if query.sql_id:
        clauses.append(f"{alias}.sql_id = %s")
        params.append(query.sql_id)
    return clauses, params


def _build_detail_filters(alias: str, **kwargs: Any) -> tuple[list[str], list[Any]]:
    """Build WHERE clauses from keyword args for detail/trend/plan queries."""
    clauses: list[str] = []
    params: list[Any] = []
    tenant_id = kwargs.get("tenant_id")
    if tenant_id is not None:
        clauses.append(f"{alias}.tenant_id = %s")
        params.append(tenant_id)
    elif kwargs.get("datasource_id") is not None:
        clauses.append(f"{alias}.datasource_id = %s")
        params.append(kwargs["datasource_id"])
    return clauses, params


# ---------------------------------------------------------------------------
# SQL text subquery (from sql_audit_samples)
# ---------------------------------------------------------------------------


def _sqltext_subquery(
    start_time_us: int,
    end_time_us: int,
    *,
    db_name: str | None = None,
    keyword: str | None = None,
) -> tuple[str, list[Any]]:
    lookback_start_us = _lookback_start_us(end_time_us, SQLTEXT_LOOKBACK_WINDOW_US)
    clauses = [
        "request_time >= %s",
        "request_time < %s",
    ]
    params: list[Any] = [lookback_start_us, end_time_us]
    if db_name:
        clauses.append("db_name = %s")
        params.append(db_name)
    if keyword:
        clauses.append("query_sql LIKE %s")
        params.append(f"%{keyword}%")
    return (
        f"""
        SELECT
          datasource_id,
          sql_id,
          MAX(query_sql) AS sql_text,
          MAX(db_name) AS db_name
        FROM sql_audit_samples
        WHERE {" AND ".join(clauses)}
        GROUP BY datasource_id, sql_id
    """,
        params,
    )


# ---------------------------------------------------------------------------
# Category list queries
# ---------------------------------------------------------------------------


async def list_monitor_category(
    query: MonitorCategoryQuery,
) -> tuple[list[dict[str, Any]], str | None, bool]:
    if query.end_time_us <= query.start_time_us:
        raise ValueError("end_time_us must be greater than start_time_us")

    compare_start_time_us, compare_end_time_us = _derive_compare_window(query)
    datasource = _get_monitor_datasource()
    builder = {
        schemas.SqlMonitorCategory.TOP_SQL: _build_top_sql_query,
        schemas.SqlMonitorCategory.SLOW_SQL: _build_slow_sql_query,
        schemas.SqlMonitorCategory.NEW_SQL: _build_new_sql_query,
        schemas.SqlMonitorCategory.REGRESSED_SQL: _build_regressed_sql_query,
        schemas.SqlMonitorCategory.PLAN_CHANGED_SQL: _build_plan_changed_sql_query,
    }[query.category]
    sql, params = builder(query, compare_start_time_us, compare_end_time_us)

    from app.db.connection import get_db_pool

    result = await get_db_pool().execute_query(
        datasource,
        sql,
        role=datasource.tenant_role,
        params=params,
    )
    rows = [dict(row) for row in result.get("rows", [])]

    # Cursor pagination: fetch limit+1 rows, use the extra to detect has_more
    has_more = len(rows) > query.limit
    items = rows[: query.limit]
    next_cursor: str | None = None
    if has_more and items:
        last = items[-1]
        sort_key = _CATEGORY_SORT_KEY[query.category]
        next_cursor = _encode_cursor(last.get(sort_key), last.get("sql_id", ""))
    return items, next_cursor, has_more


# ---------------------------------------------------------------------------
# Detail / Trend / Plan queries
# ---------------------------------------------------------------------------


async def get_monitor_sql_detail(
    *,
    sql_id: str,
    start_time_us: int,
    end_time_us: int,
    datasource_id: int | None = None,
    tenant_id: int | None = None,
) -> dict[str, Any] | None:
    datasource = _get_monitor_datasource()
    sqltext_subquery, sqltext_params = _sqltext_subquery(start_time_us, end_time_us)

    filters: list[str] = ["a.sql_id = %s"]
    params: list[Any] = [sql_id]
    extra_filters, extra_params = _build_detail_filters(
        "a",
        datasource_id=datasource_id,
        tenant_id=tenant_id,
    )
    filters.extend(extra_filters)
    params.extend(extra_params)
    filter_sql = " AND ".join(filters)

    sql = f"""
        /* sql_analysis:monitor_detail */
        SELECT
          a.sql_id,
          a.datasource_id,
          MAX(a.tenant_id) AS ob_tenant_id,
          MAX(a.tenant_name) AS tenant_name,
          MAX(a.db_name) AS db_name,
          MAX(a.user_name) AS user_name,
          COALESCE(
            NULLIF(MAX(st.query_sql), ''),
            NULLIF(MAX(t.sql_text), '')
          ) AS sql_text,
          SUM(a.executions) AS executions,
          ROUND(SUM(a.sum_elapsed_us) / NULLIF(SUM(a.executions), 0), 2) AS avg_elapsed_time_us,
          ROUND(SUM(a.sum_cpu_us) / NULLIF(SUM(a.executions), 0), 2) AS avg_execute_time_us,
          MAX(a.max_elapsed_us) AS max_elapsed_time_us,
          MAX(a.bucket_start_us) AS latest_request_time_us,
          COUNT(DISTINCT pd.plan_hash) AS plan_count
        FROM sql_audit_stat a
        LEFT JOIN sql_text_store st
          ON st.datasource_id = a.datasource_id
         AND st.sql_id = a.sql_id
        LEFT JOIN ({sqltext_subquery}) t
          ON t.datasource_id = a.datasource_id
         AND t.sql_id = a.sql_id
        LEFT JOIN plan_detail_store pd
          ON pd.datasource_id = a.datasource_id
         AND pd.sql_id = a.sql_id
        WHERE a.bucket_start_us >= %s
          AND a.bucket_start_us < %s
          AND {filter_sql}
        GROUP BY a.sql_id, a.tenant_id
        LIMIT 1
    """
    from app.db.connection import get_db_pool

    result = await get_db_pool().execute_query(
        datasource,
        sql,
        role=datasource.tenant_role,
        params=[*sqltext_params, start_time_us, end_time_us, *params],
    )
    rows = result.get("rows", [])
    return dict(rows[0]) if rows else None


async def get_monitor_sql_trend(
    *,
    sql_id: str,
    start_time_us: int,
    end_time_us: int,
    datasource_id: int | None = None,
    tenant_id: int | None = None,
) -> list[dict[str, Any]]:
    datasource = _get_monitor_datasource()
    filters: list[str] = ["a.sql_id = %s"]
    params: list[Any] = [sql_id]
    extra_filters, extra_params = _build_detail_filters(
        "a",
        datasource_id=datasource_id,
        tenant_id=tenant_id,
    )
    filters.extend(extra_filters)
    params.extend(extra_params)
    filter_sql = " AND ".join(filters)

    sql = f"""
        /* sql_analysis:monitor_trend */
        SELECT
          a.bucket_start_us,
          SUM(a.executions) AS executions,
          ROUND(SUM(a.sum_elapsed_us) / NULLIF(SUM(a.executions), 0), 2) AS avg_elapsed_time_us,
          SUM(a.sum_elapsed_us) AS total_elapsed_time_us,
          ROUND(SUM(a.sum_cpu_us) / NULLIF(SUM(a.executions), 0), 2) AS avg_execute_time_us
        FROM sql_audit_stat a
        WHERE a.bucket_start_us >= %s
          AND a.bucket_start_us < %s
          AND {filter_sql}
        GROUP BY a.bucket_start_us
        ORDER BY a.bucket_start_us ASC
    """
    from app.db.connection import get_db_pool

    result = await get_db_pool().execute_query(
        datasource,
        sql,
        role=datasource.tenant_role,
        params=[start_time_us, end_time_us, *params],
    )
    return [dict(row) for row in result.get("rows", [])]


async def _query_monitor_plan_history(
    *,
    datasource: models.DataSource,
    sql_id: str,
    start_time_us: int,
    end_time_us: int,
    datasource_id: int | None = None,
    tenant_id: int | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:

    detail_filters: list[str] = ["pd.sql_id = %s"]
    detail_params: list[Any] = [sql_id]
    if datasource_id is not None:
        detail_filters.append("pd.datasource_id = %s")
        detail_params.append(datasource_id)
    if tenant_id is not None:
        detail_filters.append("pd.tenant_id = %s")
        detail_params.append(tenant_id)
    detail_filter_sql = " AND ".join(detail_filters)

    sql = f"""
        /* sql_analysis:monitor_plan_history */
        SELECT
          pd.tenant_id,
          pd.sql_id,
          pd.plan_id,
          pd.plan_hash,
          NULL AS executions,
          NULL AS avg_exe_usec,
          NULL AS elapsed_time,
          NULL AS execute_time,
          NULL AS table_scan,
          CAST(pd.created_at AS CHAR) AS last_active_time,
          COALESCE(NULLIF(st.query_sql, ''), pd.query_sql) AS query_sql
        FROM plan_detail_store pd
        LEFT JOIN sql_text_store st
          ON st.datasource_id = pd.datasource_id
         AND st.sql_id = pd.sql_id
        WHERE {detail_filter_sql}
        ORDER BY pd.created_at DESC
        LIMIT %s
    """
    from app.db.connection import get_db_pool

    result = await get_db_pool().execute_query(
        datasource,
        sql,
        role=datasource.tenant_role,
        params=[*detail_params, limit],
    )
    return [dict(row) for row in result.get("rows", [])]


async def list_monitor_plan_history(
    *,
    sql_id: str,
    start_time_us: int,
    end_time_us: int,
    datasource_id: int | None = None,
    tenant_id: int | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    datasource = _get_monitor_datasource()
    return await _query_monitor_plan_history(
        datasource=datasource,
        sql_id=sql_id,
        start_time_us=start_time_us,
        end_time_us=end_time_us,
        datasource_id=datasource_id,
        tenant_id=tenant_id,
        limit=limit,
    )


async def _query_monitor_plan_explain_rows(
    *,
    datasource: models.DataSource,
    sql_id: str,
    plan_id: int,
    datasource_id: int | None = None,
    tenant_id: int | None = None,
) -> list[dict[str, Any]]:
    filters: list[str] = ["sql_id = %s", "plan_id = %s"]
    params: list[Any] = [sql_id, plan_id]
    if datasource_id is not None:
        filters.append("datasource_id = %s")
        params.append(datasource_id)
    if tenant_id is not None:
        filters.append("tenant_id = %s")
        params.append(tenant_id)

    # Primary: plan_detail_store (new architecture)
    sql = f"""
        SELECT plan_explain
        FROM plan_detail_store
        WHERE {" AND ".join(filters)}
        LIMIT 1
    """
    from app.db.connection import get_db_pool

    result = await get_db_pool().execute_query(
        datasource,
        sql,
        role=datasource.tenant_role,
        params=params,
    )
    return [dict(row) for row in result.get("rows", [])]


async def get_monitor_plan_explain(
    *,
    sql_id: str,
    plan_id: int | None = None,
    start_time_us: int,
    end_time_us: int,
    datasource_id: int | None = None,
    tenant_id: int | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Get plan explain from plan_detail_store."""
    datasource = _get_monitor_datasource()

    resolved_plan_id = plan_id
    if resolved_plan_id is None:
        history = await list_monitor_plan_history(
            sql_id=sql_id,
            start_time_us=start_time_us,
            end_time_us=end_time_us,
            datasource_id=datasource_id,
            tenant_id=tenant_id,
            limit=1,
        )
        if history:
            resolved_plan_id = history[0].get("plan_id")
    if resolved_plan_id is None:
        return "unavailable", []

    rows = await _query_monitor_plan_explain_rows(
        datasource=datasource,
        sql_id=sql_id,
        plan_id=resolved_plan_id,
        datasource_id=datasource_id,
        tenant_id=tenant_id,
    )
    if not rows:
        return "unavailable", []

    raw_explain = rows[0].get("plan_explain")
    if not raw_explain:
        return "unavailable", []

    try:
        items = json.loads(raw_explain) if isinstance(raw_explain, str) else raw_explain
    except (json.JSONDecodeError, TypeError):
        return "unavailable", []

    if not isinstance(items, list):
        return "unavailable", []

    normalized = []
    for item in items:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "operator": item.get("operator") or "",
                "object_name": item.get("name") or item.get("object_name"),
                "cost": item.get("cost"),
                "cardinality": item.get("rows") or item.get("cardinality"),
                "plan_line_id": item.get("plan_line_id"),
                "parent_id": item.get("parent_id"),
                "depth": item.get("plan_depth") or item.get("depth"),
                "property": item.get("property"),
            }
        )
    return "collector_plan_explain", normalized


# ---------------------------------------------------------------------------
# Category query builders (praxis collector schema)
# ---------------------------------------------------------------------------

# Maps each category to the primary sort column name in the result set.
# Used to encode the cursor from the last row.
_CATEGORY_SORT_KEY: dict[schemas.SqlMonitorCategory, str] = {
    schemas.SqlMonitorCategory.TOP_SQL: "sum_elapsed_time_us",
    schemas.SqlMonitorCategory.SLOW_SQL: "avg_elapsed_time_us",
    schemas.SqlMonitorCategory.NEW_SQL: "sql_id",
    schemas.SqlMonitorCategory.REGRESSED_SQL: "regression_ratio",
    schemas.SqlMonitorCategory.PLAN_CHANGED_SQL: "plan_count",
}


def _build_top_sql_query(
    query: MonitorCategoryQuery,
    _compare_start_time_us: int | None,
    _compare_end_time_us: int | None,
) -> tuple[str, list[Any]]:
    clauses, params = _build_common_filters("a", query)
    sqltext_subquery, sqltext_params = _sqltext_subquery(
        query.start_time_us,
        query.end_time_us,
        db_name=query.db_name,
        keyword=query.keyword,
    )
    where_sql = ""
    if clauses:
        where_sql = " AND " + " AND ".join(clauses)
    sqltext_join = "INNER JOIN" if query.db_name or query.keyword else "LEFT JOIN"

    cursor_clause = ""
    cursor_params: list[Any] = []
    cp = _decode_cursor(query.cursor)
    if cp:
        cursor_clause = (
            "HAVING sum_elapsed_time_us < %s OR (sum_elapsed_time_us = %s AND a.sql_id > %s)"
        )
        cursor_params = [cp["v"], cp["v"], cp["id"]]

    window_seconds = max((query.end_time_us - query.start_time_us) / 1_000_000, 1)
    sql = f"""
        /* sql_analysis:top_sql */
        SELECT
          a.datasource_id,
          a.tenant_id AS ob_tenant_id,
          MAX(a.tenant_name) AS tenant_name,
          a.sql_id,
          MAX(a.db_name) AS db_name,
          COALESCE(NULLIF(MAX(st.query_sql), ''), NULLIF(MAX(t.sql_text), '')) AS sql_text,
          SUM(a.executions) AS executions,
          ROUND(SUM(a.executions) / {window_seconds}, 4) AS exec_ps,
          SUM(a.sum_elapsed_us) AS sum_elapsed_time_us,
          ROUND(SUM(a.sum_elapsed_us) / NULLIF(SUM(a.executions), 0), 2) AS avg_elapsed_time_us,
          ROUND(SUM(a.sum_cpu_us) / NULLIF(SUM(a.executions), 0), 2) AS avg_cpu_time_us,
          MAX(a.max_elapsed_us) AS max_elapsed_time_us
        FROM sql_audit_stat a
        LEFT JOIN sql_text_store st
          ON st.datasource_id = a.datasource_id
         AND st.sql_id = a.sql_id
        {sqltext_join} ({sqltext_subquery}) t
          ON t.datasource_id = a.datasource_id
         AND t.sql_id = a.sql_id
        WHERE a.bucket_start_us >= %s
          AND a.bucket_start_us < %s
          AND a.sql_id IS NOT NULL
          {where_sql}
        GROUP BY a.datasource_id, a.tenant_id, a.sql_id
        {cursor_clause}
        ORDER BY sum_elapsed_time_us DESC, a.sql_id ASC
        LIMIT %s
    """
    return sql, [
        *sqltext_params,
        query.start_time_us,
        query.end_time_us,
        *params,
        *cursor_params,
        query.limit + 1,
    ]


def _build_slow_sql_query(
    query: MonitorCategoryQuery,
    _compare_start_time_us: int | None,
    _compare_end_time_us: int | None,
) -> tuple[str, list[Any]]:
    clauses, params = _build_common_filters("a", query)
    sqltext_subquery, sqltext_params = _sqltext_subquery(
        query.start_time_us,
        query.end_time_us,
        db_name=query.db_name,
        keyword=query.keyword,
    )
    where_sql = ""
    if clauses:
        where_sql = " AND " + " AND ".join(clauses)
    sqltext_join = "INNER JOIN" if query.db_name or query.keyword else "LEFT JOIN"

    cursor_clause = ""
    cursor_params: list[Any] = []
    cp = _decode_cursor(query.cursor)
    if cp:
        cursor_clause = (
            f"HAVING avg_elapsed_time_us >= {int(query.slow_threshold_us)}"
            " AND (avg_elapsed_time_us < %s OR (avg_elapsed_time_us = %s AND a.sql_id > %s))"
        )
        cursor_params = [cp["v"], cp["v"], cp["id"]]
    else:
        cursor_clause = f"HAVING avg_elapsed_time_us >= {int(query.slow_threshold_us)}"

    window_seconds = max((query.end_time_us - query.start_time_us) / 1_000_000, 1)
    sql = f"""
        /* sql_analysis:slow_sql */
        SELECT
          a.datasource_id,
          a.tenant_id AS ob_tenant_id,
          MAX(a.tenant_name) AS tenant_name,
          a.sql_id,
          MAX(a.db_name) AS db_name,
          COALESCE(NULLIF(MAX(st.query_sql), ''), NULLIF(MAX(t.sql_text), '')) AS sql_text,
          SUM(a.executions) AS executions,
          ROUND(SUM(a.executions) / {window_seconds}, 4) AS exec_ps,
          SUM(a.sum_elapsed_us) AS sum_elapsed_time_us,
          ROUND(SUM(a.sum_elapsed_us) / NULLIF(SUM(a.executions), 0), 2) AS avg_elapsed_time_us,
          ROUND(SUM(a.sum_cpu_us) / NULLIF(SUM(a.executions), 0), 2) AS avg_cpu_time_us,
          MAX(a.max_elapsed_us) AS max_elapsed_time_us
        FROM sql_audit_stat a
        LEFT JOIN sql_text_store st
          ON st.datasource_id = a.datasource_id
         AND st.sql_id = a.sql_id
        {sqltext_join} ({sqltext_subquery}) t
          ON t.datasource_id = a.datasource_id
         AND t.sql_id = a.sql_id
        WHERE a.bucket_start_us >= %s
          AND a.bucket_start_us < %s
          AND a.sql_id IS NOT NULL
          {where_sql}
        GROUP BY a.datasource_id, a.tenant_id, a.sql_id
        {cursor_clause}
        ORDER BY avg_elapsed_time_us DESC, a.sql_id ASC
        LIMIT %s
    """
    return sql, [
        *sqltext_params,
        query.start_time_us,
        query.end_time_us,
        *params,
        *cursor_params,
        query.limit + 1,
    ]


def _build_new_sql_query(
    query: MonitorCategoryQuery,
    compare_start_time_us: int | None,
    compare_end_time_us: int | None,
) -> tuple[str, list[Any]]:
    """New SQL: sql_ids appearing in current window but not in compare window."""
    if compare_start_time_us is None or compare_end_time_us is None:
        raise ValueError("new_sql requires a compare window")
    clauses, filter_params = _build_common_filters("cur", query)
    if query.db_name:
        clauses.append("cur.db_name = %s")
        filter_params.append(query.db_name)
    if query.keyword:
        clauses.append("cur.query_sql LIKE %s")
        filter_params.append(f"%{query.keyword}%")
    where_sql = ""
    if clauses:
        where_sql = " AND " + " AND ".join(clauses)

    cursor_clause = ""
    cursor_params: list[Any] = []
    cp = _decode_cursor(query.cursor)
    if cp:
        cursor_clause = "AND cur.sql_id > %s"
        cursor_params = [cp["id"]]

    sql = f"""
        /* sql_analysis:new_sql */
        SELECT
          cur.datasource_id,
          cur.tenant_id AS ob_tenant_id,
          MAX(cur.tenant_name) AS tenant_name,
          cur.sql_id,
          MAX(cur.db_name) AS db_name,
          COALESCE(NULLIF(MAX(st.query_sql), ''), NULLIF(MAX(cur.query_sql), '')) AS sql_text
        FROM sql_audit_samples cur
        LEFT JOIN sql_text_store st
          ON st.datasource_id = cur.datasource_id
         AND st.sql_id = cur.sql_id
        WHERE cur.request_time >= %s
          AND cur.request_time < %s
          AND cur.sql_id IS NOT NULL
          {where_sql}
          AND NOT EXISTS (
            SELECT 1
            FROM sql_audit_samples hist
            WHERE hist.datasource_id = cur.datasource_id
              AND hist.sql_id = cur.sql_id
              AND hist.request_time >= %s
              AND hist.request_time < %s
          )
          {cursor_clause}
        GROUP BY cur.datasource_id, cur.tenant_id, cur.sql_id
        ORDER BY cur.sql_id ASC
        LIMIT %s
    """
    return sql, [
        query.start_time_us,
        query.end_time_us,
        *filter_params,
        compare_start_time_us,
        compare_end_time_us,
        *cursor_params,
        query.limit + 1,
    ]


def _build_regressed_sql_query(
    query: MonitorCategoryQuery,
    compare_start_time_us: int | None,
    compare_end_time_us: int | None,
) -> tuple[str, list[Any]]:
    if compare_start_time_us is None or compare_end_time_us is None:
        raise ValueError("regressed_sql requires a compare window")
    clauses, common_params = _build_common_filters("a", query)
    sqltext_subquery, sqltext_params = _sqltext_subquery(
        query.start_time_us,
        query.end_time_us,
        db_name=query.db_name,
        keyword=query.keyword,
    )
    where_sql = ""
    if clauses:
        where_sql = " AND " + " AND ".join(clauses)
    sqltext_join = "INNER JOIN" if query.db_name or query.keyword else "LEFT JOIN"
    window_seconds = max((query.end_time_us - query.start_time_us) / 1_000_000, 1)

    cursor_clause = ""
    cursor_params: list[Any] = []
    cp = _decode_cursor(query.cursor)
    if cp:
        cursor_clause = "AND (regression_ratio < %s OR (regression_ratio = %s AND cur.sql_id > %s))"
        cursor_params = [cp["v"], cp["v"], cp["id"]]

    sql = f"""
        /* sql_analysis:regressed_sql */
        SELECT
          cur.datasource_id,
          cur.tenant_id AS ob_tenant_id,
          cur.tenant_name,
          cur.sql_id,
          MAX(t.db_name) AS db_name,
          COALESCE(NULLIF(MAX(st.query_sql), ''), NULLIF(MAX(t.sql_text), '')) AS sql_text,
          cur.exec AS executions,
          ROUND(cur.exec / {window_seconds}, 4) AS exec_ps,
          cur.elapsed_time_us AS sum_elapsed_time_us,
          ROUND(cur.elapsed_time_us / NULLIF(cur.exec, 0), 2) AS avg_elapsed_time_us,
          ROUND(cur.cpu_time_us / NULLIF(cur.exec, 0), 2) AS avg_cpu_time_us,
          cur.max_elapsed_time_us AS max_elapsed_time_us,
          ROUND(
            (cur.elapsed_time_us / NULLIF(cur.exec, 0)) /
            NULLIF((base.elapsed_time_us / NULLIF(base.exec, 0)), 0),
            4
          ) AS regression_ratio
        FROM (
          SELECT
            a.datasource_id,
            a.tenant_id,
            MAX(a.tenant_name) AS tenant_name,
            a.sql_id,
            SUM(a.executions) AS exec,
            SUM(a.sum_elapsed_us) AS elapsed_time_us,
            SUM(a.sum_cpu_us) AS cpu_time_us,
            MAX(a.max_elapsed_us) AS max_elapsed_time_us
          FROM sql_audit_stat a
          WHERE a.bucket_start_us >= %s
            AND a.bucket_start_us < %s
            AND a.sql_id IS NOT NULL
            {where_sql}
          GROUP BY a.datasource_id, a.tenant_id, a.sql_id
        ) cur
        JOIN (
          SELECT
            a.datasource_id,
            a.tenant_id,
            a.sql_id,
            SUM(a.executions) AS exec,
            SUM(a.sum_elapsed_us) AS elapsed_time_us
          FROM sql_audit_stat a
          WHERE a.bucket_start_us >= %s
            AND a.bucket_start_us < %s
            AND a.sql_id IS NOT NULL
            {where_sql}
          GROUP BY a.datasource_id, a.tenant_id, a.sql_id
        ) base
          ON base.datasource_id = cur.datasource_id
         AND base.sql_id = cur.sql_id
        {sqltext_join} ({sqltext_subquery}) t
          ON t.datasource_id = cur.datasource_id
         AND t.sql_id = cur.sql_id
        LEFT JOIN sql_text_store st
          ON st.datasource_id = cur.datasource_id
         AND st.sql_id = cur.sql_id
        WHERE (cur.elapsed_time_us / NULLIF(cur.exec, 0)) > (base.elapsed_time_us / NULLIF(base.exec, 0))
          {cursor_clause}
        ORDER BY regression_ratio DESC, cur.sql_id ASC
        LIMIT %s
    """
    return sql, [
        query.start_time_us,
        query.end_time_us,
        *common_params,
        compare_start_time_us,
        compare_end_time_us,
        *common_params,
        *sqltext_params,
        *cursor_params,
        query.limit + 1,
    ]


def _build_plan_changed_sql_query(
    query: MonitorCategoryQuery,
    _compare_start_time_us: int | None,
    _compare_end_time_us: int | None,
) -> tuple[str, list[Any]]:
    clauses, params = _build_common_filters("pd", query)
    sqltext_subquery, sqltext_params = _sqltext_subquery(
        query.start_time_us,
        query.end_time_us,
        db_name=query.db_name,
        keyword=query.keyword,
    )
    where_sql = ""
    if clauses:
        where_sql = " AND " + " AND ".join(clauses)
    sqltext_join = "INNER JOIN" if query.db_name or query.keyword else "LEFT JOIN"

    cursor_clause = ""
    cursor_params: list[Any] = []
    cp = _decode_cursor(query.cursor)
    if cp:
        cursor_clause = "AND (plan_count < %s OR (plan_count = %s AND pd.sql_id > %s))"
        cursor_params = [cp["v"], cp["v"], cp["id"]]

    sql = f"""
        /* sql_analysis:plan_changed_sql */
        SELECT
          pd.datasource_id,
          pd.tenant_id AS ob_tenant_id,
          pd.sql_id,
          MAX(t.db_name) AS db_name,
          COALESCE(NULLIF(MAX(st.query_sql), ''), NULLIF(MAX(t.sql_text), '')) AS sql_text,
          COUNT(DISTINCT pd.plan_hash) AS plan_count
        FROM plan_detail_store pd
        LEFT JOIN sql_text_store st
          ON st.datasource_id = pd.datasource_id
         AND st.sql_id = pd.sql_id
        {sqltext_join} ({sqltext_subquery}) t
          ON t.datasource_id = pd.datasource_id
         AND t.sql_id = pd.sql_id
        WHERE pd.created_at >= FROM_UNIXTIME(%s / 1000000)
          AND pd.created_at < FROM_UNIXTIME(%s / 1000000)
          AND pd.sql_id IS NOT NULL
          {where_sql}
        GROUP BY pd.datasource_id, pd.tenant_id, pd.sql_id
        HAVING COUNT(DISTINCT pd.plan_hash) > 1
          {cursor_clause}
        ORDER BY plan_count DESC, pd.sql_id ASC
        LIMIT %s
    """
    return sql, [
        *sqltext_params,
        query.start_time_us,
        query.end_time_us,
        *params,
        *cursor_params,
        query.limit + 1,
    ]
