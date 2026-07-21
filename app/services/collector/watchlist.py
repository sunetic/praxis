from app.core.logging import get_logger
from app.db.connection import DBConnectionPool
from app.models.models import DataSource
from app.services.collector.config import cfg

logger = get_logger("collector.watchlist")
_pool = DBConnectionPool()


async def add_or_renew(target_ds: DataSource, datasource_id: int, tenant_id: int, sql_ids: list[str], added_by: str) -> None:
    if not sql_ids:
        return
    for sql_id in sql_ids:
        await _pool.execute_query(
            target_ds,
            "INSERT INTO collector_watchlist "
            "(datasource_id, tenant_id, sql_id, added_by, idle_count, last_active_at) "
            "VALUES (%s, %s, %s, %s, 0, NOW(3)) "
            "ON DUPLICATE KEY UPDATE idle_count=0, last_active_at=NOW(3)",
            params=[datasource_id, tenant_id, sql_id, added_by],
        )


async def increment_idle(target_ds: DataSource, datasource_id: int, tenant_id: int, sql_ids: list[str]) -> None:
    if not sql_ids:
        return
    for batch_start in range(0, len(sql_ids), cfg.watchlist_batch_size):
        batch = sql_ids[batch_start : batch_start + cfg.watchlist_batch_size]
        placeholders = ", ".join(["%s"] * len(batch))
        await _pool.execute_query(
            target_ds,
            f"UPDATE collector_watchlist SET idle_count = idle_count + 1 "
            f"WHERE datasource_id = %s AND tenant_id = %s AND sql_id IN ({placeholders})",
            params=[datasource_id, tenant_id] + batch,
        )


async def evict_idle(target_ds: DataSource, datasource_id: int) -> int:
    result = await _pool.execute_query(
        target_ds,
        "DELETE FROM collector_watchlist WHERE datasource_id = %s AND idle_count >= %s",
        params=[datasource_id, cfg.watchlist_idle_windows],
    )
    evicted = result.get("row_count", 0)
    if evicted:
        logger.info("watchlist_evict datasource_id=%s evicted=%s", datasource_id, evicted)
    return evicted


async def enforce_cap(target_ds: DataSource, datasource_id: int) -> None:
    count_result = await _pool.execute_query(
        target_ds,
        "SELECT COUNT(*) AS cnt FROM collector_watchlist WHERE datasource_id = %s",
        params=[datasource_id],
    )
    _r = count_result["rows"][0] if count_result["row_count"] > 0 else None
    count = (_r["cnt"] if isinstance(_r, dict) else _r[0]) if _r is not None else 0
    if count <= cfg.max_watchlist_size:
        return
    overflow = count - cfg.max_watchlist_size
    await _pool.execute_query(
        target_ds,
        "DELETE FROM collector_watchlist WHERE datasource_id = %s "
        "ORDER BY last_active_at ASC LIMIT %s",
        params=[datasource_id, overflow],
    )
    logger.info("watchlist_cap_enforced datasource_id=%s removed=%s", datasource_id, overflow)


async def get_active_ids(target_ds: DataSource, datasource_id: int, tenant_id: int) -> list[str]:
    result = await _pool.execute_query(
        target_ds,
        "SELECT sql_id FROM collector_watchlist "
        "WHERE datasource_id = %s AND tenant_id = %s "
        "ORDER BY last_active_at DESC LIMIT %s",
        params=[datasource_id, tenant_id, cfg.max_watchlist_size],
    )
    rows = result["rows"]
    if not rows:
        return []
    if isinstance(rows[0], dict):
        return [row["sql_id"] for row in rows]
    return [row[0] for row in rows]


async def get_size(target_ds: DataSource, datasource_id: int) -> int:
    result = await _pool.execute_query(
        target_ds,
        "SELECT COUNT(*) FROM collector_watchlist WHERE datasource_id = %s",
        params=[datasource_id],
    )
    if result["row_count"] == 0:
        return 0
    _r = result["rows"][0]
    return list(_r.values())[0] if isinstance(_r, dict) else _r[0]
