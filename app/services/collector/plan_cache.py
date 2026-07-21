import json

from app.core.logging import get_logger
from app.db.connection import DBConnectionPool
from app.models.models import DataSource

logger = get_logger("collector.plan_cache")
_pool = DBConnectionPool()

_EXPLAIN_BATCH_SIZE = 50
_SQL_ID_BATCH_SIZE = 200


async def upsert_plan_details(
    target_ds: DataSource,
    datasource_id: int,
    plan_rows: list[dict],
    explains: dict[int, str | None],
) -> int:
    """Batch INSERT IGNORE into plan_detail_store. Returns count of new rows inserted."""
    if not plan_rows:
        return 0
    count = 0
    for row in plan_rows:
        plan_id = row["PLAN_ID"]
        plan_explain = explains.get(plan_id)
        query_sql = row.get("QUERY_SQL") or None
        await _pool.execute_query(
            target_ds,
            """
            INSERT IGNORE INTO plan_detail_store
              (datasource_id, plan_id, sql_id, tenant_id, plan_hash, plan_explain, query_sql)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            params=[
                datasource_id, plan_id, row["SQL_ID"], row["TENANT_ID"],
                row["PLAN_HASH"], plan_explain, query_sql,
            ],
        )
        count += 1
    return count


async def collect_plans_for_sql_ids(
    source_ds: DataSource,
    target_ds: DataSource,
    sql_ids: list[str],
) -> int:
    """Targeted plan collection for a given set of sql_ids. Returns count of new plan_detail_store rows."""
    if not sql_ids:
        return 0

    all_plan_rows: list[dict] = []
    for i in range(0, len(sql_ids), _SQL_ID_BATCH_SIZE):
        batch = sql_ids[i : i + _SQL_ID_BATCH_SIZE]
        placeholders = ", ".join(["%s"] * len(batch))
        try:
            result = await _pool.execute_query(
                source_ds,
                f"""
                SELECT TENANT_ID, SQL_ID, PLAN_ID, PLAN_HASH, EXECUTIONS,
                       ELAPSED_TIME, CPU_TIME, AVG_EXE_USEC, TABLE_SCAN,
                       FIRST_LOAD_TIME, LAST_ACTIVE_TIME,
                       SUBSTRING(QUERY_SQL, 1, 4096) AS QUERY_SQL
                FROM oceanbase.GV$OB_PLAN_CACHE_PLAN_STAT
                WHERE SQL_ID IN ({placeholders})
                """,
                params=batch,
            )
        except Exception as e:
            logger.warning("plan_stat_batch_failed batch_start=%s error=%s", i, e)
            continue
        cols = result["columns"]
        for r in result["rows"]:
            all_plan_rows.append(r if isinstance(r, dict) else dict(zip(cols, r)))

    if not all_plan_rows:
        return 0

    # Deduplicate by (SQL_ID, PLAN_HASH): GV$ returns one row per OBServer node for the same plan
    seen: set[tuple] = set()
    deduped_rows: list[dict] = []
    for r in all_plan_rows:
        key = (r["SQL_ID"], r["PLAN_HASH"])
        if key not in seen:
            seen.add(key)
            deduped_rows.append(r)
    all_plan_rows = deduped_rows

    # Check which plan_hashes are already in plan_detail_store
    all_plan_hashes = [r["PLAN_HASH"] for r in all_plan_rows]
    existing_plan_hashes: set[int] = set()
    for i in range(0, len(all_plan_hashes), 500):
        batch = all_plan_hashes[i : i + 500]
        ph = ", ".join(["%s"] * len(batch))
        try:
            ex_result = await _pool.execute_query(
                target_ds,
                f"SELECT plan_hash FROM plan_detail_store WHERE datasource_id = %s AND plan_hash IN ({ph})",
                params=[source_ds.id, *batch],
            )
            for r in ex_result["rows"]:
                rd = r if isinstance(r, dict) else dict(zip(ex_result["columns"], r))
                existing_plan_hashes.add(rd["plan_hash"])
        except Exception as e:
            logger.warning("plan_detail_check_failed: %s", e)

    new_rows = [r for r in all_plan_rows if r["PLAN_HASH"] not in existing_plan_hashes]

    # Fetch explains only for new plans
    explains: dict[int, str | None] = {}
    new_plan_ids = [r["PLAN_ID"] for r in new_rows]
    for i in range(0, len(new_plan_ids), _EXPLAIN_BATCH_SIZE):
        batch_ids = new_plan_ids[i : i + _EXPLAIN_BATCH_SIZE]
        batch_explains = await _fetch_explains_batch(source_ds, batch_ids)
        explains.update(batch_explains)

    # Write new plan details to plan_detail_store
    new_detail_count = await upsert_plan_details(target_ds, source_ds.id, new_rows, explains)

    logger.info(
        "plan_collect sql_ids=%s total_plans=%s new_plans=%s",
        len(sql_ids), len(all_plan_rows), new_detail_count,
    )
    return new_detail_count


async def _fetch_explains_batch(
    source_ds: DataSource, plan_ids: list[int],
) -> dict[int, str | None]:
    """Fetch plan explains for multiple plan_ids in one query."""
    if not plan_ids:
        return {}
    placeholders = ", ".join(["%s"] * len(plan_ids))
    try:
        result = await _pool.execute_query(
            source_ds,
            f"""
            SELECT plan_id, plan_line_id, operator, name, rows, cost, property, plan_depth
            FROM oceanbase.GV$OB_PLAN_CACHE_PLAN_EXPLAIN
            WHERE plan_id IN ({placeholders})
            ORDER BY plan_id, plan_line_id ASC
            """,
            params=plan_ids,
        )
    except Exception as e:
        logger.warning("explain_batch_failed plan_ids=%s error=%s", plan_ids[:5], e)
        return {}

    if result["row_count"] == 0:
        return {pid: None for pid in plan_ids}

    cols = result["columns"]
    raw = result["rows"]
    grouped: dict[int, list[dict]] = {}
    for r in raw:
        row = r if isinstance(r, dict) else dict(zip(cols, r))
        pid = row["plan_id"]
        grouped.setdefault(pid, []).append(
            {k: v for k, v in row.items() if k != "plan_id"}
        )

    return {
        pid: json.dumps(grouped[pid], ensure_ascii=False, default=str) if pid in grouped else None
        for pid in plan_ids
    }

