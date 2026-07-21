import time

from app.core.logging import get_logger
from app.db.connection import DBConnectionPool
from app.models.models import DataSource
from app.services.collector.config import cfg

logger = get_logger("collector.cleanup")
_pool = DBConnectionPool()

_BATCH = 1000
_TIMEOUT_SECS = 300


async def _delete_batched(target_ds: DataSource, table: str, time_col: str, retention_days: int) -> int:
    total = 0
    deadline = time.monotonic() + _TIMEOUT_SECS
    while True:
        if time.monotonic() > deadline:
            logger.warning("cleanup_timeout table=%s deleted=%s", table, total)
            break
        result = await _pool.execute_query(
            target_ds,
            f"DELETE FROM {table} WHERE {time_col} < NOW() - INTERVAL %s DAY LIMIT %s",
            params=[retention_days, _BATCH],
        )
        deleted = result.get("row_count", 0)
        total += deleted
        if deleted < _BATCH:
            break
    return total


async def _delete_orphans(
    target_ds: DataSource,
    detail_table: str,
    detail_id_col: str,
    stat_table: str,
    stat_id_col: str,
) -> int:
    """Delete rows from detail_table where the id is not referenced by any row in stat_table.

    Uses SELECT-then-DELETE to avoid OceanBase's restriction on DELETE ... WHERE subquery ... LIMIT.
    Both tables must have a datasource_id column and the detail_id_col as the orphan key.
    """
    total = 0
    deadline = time.monotonic() + _TIMEOUT_SECS
    while True:
        if time.monotonic() > deadline:
            logger.warning("orphan_cleanup_timeout table=%s deleted=%s", detail_table, total)
            break
        select_result = await _pool.execute_query(
            target_ds,
            f"""
            SELECT {detail_table}.datasource_id, {detail_table}.{detail_id_col}
            FROM {detail_table}
            WHERE NOT EXISTS (
                SELECT 1 FROM {stat_table} s
                WHERE s.datasource_id = {detail_table}.datasource_id
                  AND s.{stat_id_col} = {detail_table}.{detail_id_col}
            )
            LIMIT %s
            """,
            params=[_BATCH],
        )
        rows = select_result.get("rows", [])
        if not rows:
            break
        cols = select_result.get("columns", [])
        pairs = [
            (r["datasource_id"], r[detail_id_col])
            if isinstance(r, dict)
            else (dict(zip(cols, r))["datasource_id"], dict(zip(cols, r))[detail_id_col])
            for r in rows
        ]
        ph = ", ".join(["(%s, %s)"] * len(pairs))
        flat_params = [v for pair in pairs for v in pair]
        result = await _pool.execute_query(
            target_ds,
            f"DELETE FROM {detail_table} WHERE (datasource_id, {detail_id_col}) IN ({ph})",
            params=flat_params,
        )
        deleted = result.get("row_count", 0)
        total += deleted
        if len(rows) < _BATCH:
            break
    return total


async def run_cleanup(target_ds: DataSource) -> dict:
    stat_deleted = await _delete_batched(
        target_ds, "sql_audit_stat", "collected_at", cfg.sql_audit_stat_retention_days
    )
    sample_deleted = await _delete_batched(
        target_ds, "sql_audit_samples", "sampled_at", cfg.sql_audit_sample_retention_days
    )

    sql_text_store_deleted = await _delete_orphans(
        target_ds, "sql_text_store", "sql_id", "sql_audit_stat", "sql_id"
    )
    plan_detail_store_deleted = await _delete_orphans(
        target_ds, "plan_detail_store", "sql_id", "sql_audit_stat", "sql_id"
    )

    logger.info(
        "cleanup_done stat=%s sample=%s sql_text_store=%s plan_detail_store=%s",
        stat_deleted, sample_deleted, sql_text_store_deleted, plan_detail_store_deleted,
    )
    return {
        "sql_audit_stat_deleted": stat_deleted,
        "sql_audit_samples_deleted": sample_deleted,
        "sql_text_store_deleted": sql_text_store_deleted,
        "plan_detail_store_deleted": plan_detail_store_deleted,
    }
