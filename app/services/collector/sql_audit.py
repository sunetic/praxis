from datetime import datetime, timezone

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.connection import DBConnectionPool
from app.models.models import DataSource
from app.services.collector import checkpoint, watchlist
from app.services.collector.config import cfg

logger = get_logger("collector.sql_audit")
_pool = DBConnectionPool()


def _get_excluded_dbs() -> tuple[str, ...]:
    base = ("oceanbase", "information_schema", "mysql", "performance_schema", "sys")
    collector_db = get_settings().monitor_db_database
    if collector_db and collector_db not in base:
        return (*base, collector_db)
    return base


_EXCLUDED_DBS = _get_excluded_dbs()
_EXCLUDED_PLACEHOLDER = ", ".join(["%s"] * len(_EXCLUDED_DBS))
_METADATA_LOOKBACK_WINDOW_US = 60 * 60 * 1_000_000

# OceanBase GV$OB_SQL_AUDIT uses uppercase column names.
# cpu_time does not exist; EXECUTE_TIME is used instead.
# logical_reads = MEMSTORE_READ_ROW_COUNT + SSSTORE_READ_ROW_COUNT
# physical_reads = DISK_READS

_CAUSE_TYPE_CONDITIONS = [
    ("slow_sql", lambda r: r["ELAPSED_TIME"] >= cfg.slow_sql_threshold_us),
    ("large_query", lambda r: (r["MEMSTORE_READ_ROW_COUNT"] + r["SSSTORE_READ_ROW_COUNT"]) >= cfg.large_query_rows),
    ("error_sql", lambda r: r["RET_CODE"] != 0),
    ("table_scan_slow", lambda r: r["TABLE_SCAN"] and r["ELAPSED_TIME"] >= cfg.table_scan_slow_threshold_us),
]


def _now_us() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1_000_000)


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _row_identity(row: dict) -> tuple[str, int, int]:
    return (row["SQL_ID"], int(row["TENANT_ID"]), int(row["PLAN_HASH"] or 0))


def _metadata_identity(row: dict) -> tuple[str, int]:
    return (row["SQL_ID"], int(row["TENANT_ID"]))


def _apply_metadata_candidate(target: dict[str, str], row: dict) -> None:
    tenant_name = _clean_text(row.get("TENANT_NAME"))
    db_name = _clean_text(row.get("DB_NAME"))
    user_name = _clean_text(row.get("USER_NAME"))
    query_sql = _clean_text(row.get("QUERY_SQL"))
    if tenant_name and not target["TENANT_NAME"]:
        target["TENANT_NAME"] = tenant_name
    if db_name and not target["DB_NAME"]:
        target["DB_NAME"] = db_name
    if user_name and not target["USER_NAME"]:
        target["USER_NAME"] = user_name
    if query_sql and len(query_sql) > len(target["QUERY_SQL"]):
        target["QUERY_SQL"] = query_sql


def _build_preferred_metadata(rows: list[dict]) -> dict[tuple[str, int], dict[str, str]]:
    metadata: dict[tuple[str, int], dict[str, str]] = {}
    for row in rows:
        key = _metadata_identity(row)
        current = metadata.setdefault(
            key,
            {"TENANT_NAME": "", "DB_NAME": "", "USER_NAME": "", "QUERY_SQL": ""},
        )
        _apply_metadata_candidate(current, row)
    return metadata


async def _enrich_preferred_metadata(
    source_ds: DataSource,
    metadata: dict[tuple[str, int], dict[str, str]],
    *,
    end_time_us: int,
) -> dict[tuple[str, int], dict[str, str]]:
    pending = [key for key, value in metadata.items() if not all(value.values())]
    if not pending:
        return metadata
    lookback_start_us = max(0, end_time_us - _METADATA_LOOKBACK_WINDOW_US)
    for sql_id, tenant_id in pending:
        result = await _pool.execute_query(
            source_ds,
            """
            SELECT TENANT_NAME, DB_NAME, USER_NAME, SUBSTRING(QUERY_SQL, 1, 4096) AS QUERY_SQL
            FROM oceanbase.GV$OB_SQL_AUDIT
            WHERE SQL_ID = %s
              AND TENANT_ID = %s
              AND REQUEST_TIME >= %s
              AND REQUEST_TIME < %s
              AND (
                COALESCE(TRIM(TENANT_NAME), '') != ''
                OR COALESCE(TRIM(DB_NAME), '') != ''
                OR COALESCE(TRIM(USER_NAME), '') != ''
                OR COALESCE(TRIM(QUERY_SQL), '') != ''
              )
            ORDER BY REQUEST_TIME DESC
            LIMIT 50
            """,
            params=[sql_id, tenant_id, lookback_start_us, end_time_us],
        )
        rows = result.get("rows", [])
        cols = result.get("columns", [])
        for item in rows:
            row = item if isinstance(item, dict) else dict(zip(cols, item))
            _apply_metadata_candidate(metadata[(sql_id, tenant_id)], row)
            if all(metadata[(sql_id, tenant_id)].values()):
                break
    return metadata


async def collect_threshold(
    source_ds: DataSource, target_ds: DataSource, window_start_us: int, window_end_us: int
) -> list[tuple[str, int]]:
    """GROUP BY SQL_ID with HAVING SUM(EXECUTE_TIME) > threshold. Returns (sql_id, tenant_id) pairs."""
    result = await _pool.execute_query(
        source_ds,
        f"""
        SELECT SQL_ID, TENANT_ID, TENANT_NAME, DB_NAME, USER_NAME, PLAN_HASH,
               COUNT(*) AS executions,
               SUM(ELAPSED_TIME) AS sum_elapsed_us,
               MAX(ELAPSED_TIME) AS max_elapsed_us,
               SUM(EXECUTE_TIME) AS sum_cpu_us,
               MAX(EXECUTE_TIME) AS max_cpu_us,
               SUM(MEMSTORE_READ_ROW_COUNT + SSSTORE_READ_ROW_COUNT) AS sum_logical_reads,
               MAX(MEMSTORE_READ_ROW_COUNT + SSSTORE_READ_ROW_COUNT) AS max_logical_reads,
               SUM(AFFECTED_ROWS) AS sum_affected_rows,
               MAX(AFFECTED_ROWS) AS max_affected_rows,
               SUM(RETURN_ROWS) AS sum_return_rows,
               MAX(TABLE_SCAN) AS has_table_scan,
               SUM(CASE WHEN RET_CODE != 0 THEN 1 ELSE 0 END) AS fail_count,
               SUBSTRING(QUERY_SQL, 1, 4096) AS QUERY_SQL
        FROM oceanbase.GV$OB_SQL_AUDIT
        WHERE REQUEST_TIME BETWEEN %s AND %s
          AND DB_NAME NOT IN ({_EXCLUDED_PLACEHOLDER})
          AND IS_INNER_SQL = 0
        GROUP BY SQL_ID, TENANT_ID, PLAN_HASH, TENANT_NAME, DB_NAME, USER_NAME, SUBSTRING(QUERY_SQL, 1, 4096)
        HAVING SUM(EXECUTE_TIME) > %s
        """,
        params=[window_start_us, window_end_us, *_EXCLUDED_DBS, cfg.cpu_threshold_us],
    )

    if result["row_count"] == 0:
        return []

    cols = result["columns"]
    raw = result["rows"]
    rows = [r if isinstance(r, dict) else dict(zip(cols, r)) for r in raw]
    preferred_metadata = await _enrich_preferred_metadata(
        source_ds,
        _build_preferred_metadata(rows),
        end_time_us=window_end_us,
    )
    aggregates: dict[tuple[str, int, int], dict] = {}
    matched: list[tuple[str, int]] = []

    for row in rows:
        key = _row_identity(row)
        aggregate = aggregates.get(key)
        if aggregate is None:
            aggregate = dict(row)
            aggregates[key] = aggregate
        else:
            aggregate["executions"] += row["executions"]
            aggregate["sum_elapsed_us"] += row["sum_elapsed_us"]
            aggregate["max_elapsed_us"] = max(aggregate["max_elapsed_us"], row["max_elapsed_us"])
            aggregate["sum_cpu_us"] += row["sum_cpu_us"]
            aggregate["max_cpu_us"] = max(aggregate["max_cpu_us"], row["max_cpu_us"])
            aggregate["sum_logical_reads"] += row["sum_logical_reads"]
            aggregate["max_logical_reads"] = max(aggregate["max_logical_reads"], row["max_logical_reads"])
            aggregate["sum_affected_rows"] += row["sum_affected_rows"]
            aggregate["max_affected_rows"] = max(aggregate["max_affected_rows"], row["max_affected_rows"])
            aggregate["sum_return_rows"] += row["sum_return_rows"]
            aggregate["has_table_scan"] = max(aggregate["has_table_scan"], row["has_table_scan"])
            aggregate["fail_count"] += row["fail_count"]

    for key, row in aggregates.items():
        metadata = preferred_metadata[_metadata_identity(row)]
        tenant_name = metadata["TENANT_NAME"]
        db_name = metadata["DB_NAME"]
        user_name = metadata["USER_NAME"]
        query_sql = metadata["QUERY_SQL"]
        await _pool.execute_query(
            target_ds,
            """
            INSERT INTO sql_audit_stat
              (datasource_id, tenant_id, tenant_name, sql_id, plan_hash,
               db_name, user_name, bucket_start_us, executions, sum_elapsed_us, max_elapsed_us,
               sum_cpu_us, max_cpu_us, sum_logical_reads, max_logical_reads,
               sum_affected_rows, max_affected_rows, sum_return_rows, has_table_scan,
               fail_count, collect_mode)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'threshold')
            ON DUPLICATE KEY UPDATE
              tenant_name=VALUES(tenant_name), db_name=VALUES(db_name), user_name=VALUES(user_name),
              executions=VALUES(executions), sum_elapsed_us=VALUES(sum_elapsed_us),
              max_elapsed_us=VALUES(max_elapsed_us), sum_cpu_us=VALUES(sum_cpu_us),
              max_cpu_us=VALUES(max_cpu_us), sum_logical_reads=VALUES(sum_logical_reads),
              max_logical_reads=VALUES(max_logical_reads), sum_affected_rows=VALUES(sum_affected_rows),
              max_affected_rows=VALUES(max_affected_rows), sum_return_rows=VALUES(sum_return_rows),
              has_table_scan=VALUES(has_table_scan), fail_count=VALUES(fail_count),
              collect_mode='threshold', collected_at=NOW(3)
            """,
            params=[
                source_ds.id, row["TENANT_ID"], tenant_name,
                row["SQL_ID"], row["PLAN_HASH"] or 0, db_name, user_name,
                window_start_us, row["executions"], row["sum_elapsed_us"], row["max_elapsed_us"],
                row["sum_cpu_us"], row["max_cpu_us"], row["sum_logical_reads"], row["max_logical_reads"],
                row["sum_affected_rows"], row["max_affected_rows"], row["sum_return_rows"],
                int(bool(row["has_table_scan"])), row["fail_count"],
            ],
        )
        if query_sql:
            await _pool.execute_query(
                target_ds,
                """
                INSERT INTO sql_audit_samples
                  (datasource_id, tenant_id, tenant_name, sql_id, plan_hash,
                   db_name, user_name, cause_type, request_time, elapsed_time, execute_time,
                   cpu_time, queue_time, logical_reads, physical_reads, affected_rows,
                   return_rows, table_scan, ret_code, query_sql)
                VALUES (%s,%s,%s,%s,%s,%s,%s,'threshold',%s,%s,%s,%s,0,%s,0,%s,%s,%s,0,%s)
                """,
                params=[
                    source_ds.id, row["TENANT_ID"], tenant_name,
                    row["SQL_ID"], row["PLAN_HASH"] or 0, db_name, user_name,
                    window_end_us, row["max_elapsed_us"], row["max_cpu_us"], row["max_cpu_us"],
                    row["max_logical_reads"], row["max_affected_rows"], row["sum_return_rows"],
                    int(bool(row["has_table_scan"])), query_sql,
                ],
            )
        matched.append((row["SQL_ID"], int(row["TENANT_ID"])))

    logger.info("threshold_collect window=%s-%s rows=%s", window_start_us, window_end_us, len(rows))
    return matched


async def collect_watchlist(
    source_ds: DataSource, target_ds: DataSource, tenant_ids: set[int], window_start_us: int, window_end_us: int
) -> None:
    """Targeted collection for watchlisted sql_ids across all tracked tenants."""
    all_sql_ids: list[str] = []
    for tid in tenant_ids:
        ids = await watchlist.get_active_ids(target_ds, source_ds.id, tid)
        all_sql_ids.extend(ids)
    all_sql_ids = list(set(all_sql_ids))
    if not all_sql_ids:
        return

    active: set[tuple[str, int]] = set()
    for batch_start in range(0, len(all_sql_ids), cfg.watchlist_batch_size):
        batch = all_sql_ids[batch_start : batch_start + cfg.watchlist_batch_size]
        placeholders = ", ".join(["%s"] * len(batch))
        try:
            result = await _pool.execute_query(
                source_ds,
                f"""
                SELECT SQL_ID, TENANT_ID, TENANT_NAME, DB_NAME, USER_NAME, PLAN_HASH,
                       COUNT(*) AS executions,
                       SUM(ELAPSED_TIME) AS sum_elapsed_us, MAX(ELAPSED_TIME) AS max_elapsed_us,
                       SUM(EXECUTE_TIME) AS sum_cpu_us, MAX(EXECUTE_TIME) AS max_cpu_us,
                       SUM(MEMSTORE_READ_ROW_COUNT + SSSTORE_READ_ROW_COUNT) AS sum_logical_reads,
                       MAX(MEMSTORE_READ_ROW_COUNT + SSSTORE_READ_ROW_COUNT) AS max_logical_reads,
                       SUM(AFFECTED_ROWS) AS sum_affected_rows, MAX(AFFECTED_ROWS) AS max_affected_rows,
                       SUM(RETURN_ROWS) AS sum_return_rows,
                       MAX(TABLE_SCAN) AS has_table_scan,
                       SUM(CASE WHEN RET_CODE != 0 THEN 1 ELSE 0 END) AS fail_count,
                       SUBSTRING(QUERY_SQL, 1, 4096) AS QUERY_SQL
                FROM oceanbase.GV$OB_SQL_AUDIT
                WHERE REQUEST_TIME BETWEEN %s AND %s AND SQL_ID IN ({placeholders})
                GROUP BY SQL_ID, TENANT_ID, PLAN_HASH, TENANT_NAME, DB_NAME, USER_NAME, SUBSTRING(QUERY_SQL, 1, 4096)
                """,
                params=[window_start_us, window_end_us, *batch],
            )
        except Exception as e:
            logger.warning("watchlist_batch_failed batch_start=%s error=%s", batch_start, e)
            continue

        cols = result["columns"]
        rows = [r if isinstance(r, dict) else dict(zip(cols, r)) for r in result["rows"]]
        preferred_metadata = await _enrich_preferred_metadata(
            source_ds,
            _build_preferred_metadata(rows),
            end_time_us=window_end_us,
        )
        aggregates: dict[tuple[str, int, int], dict] = {}
        for row in rows:
            key = _row_identity(row)
            aggregate = aggregates.get(key)
            if aggregate is None:
                aggregate = dict(row)
                aggregates[key] = aggregate
            else:
                aggregate["executions"] += row["executions"]
                aggregate["sum_elapsed_us"] += row["sum_elapsed_us"]
                aggregate["max_elapsed_us"] = max(aggregate["max_elapsed_us"], row["max_elapsed_us"])
                aggregate["sum_cpu_us"] += row["sum_cpu_us"]
                aggregate["max_cpu_us"] = max(aggregate["max_cpu_us"], row["max_cpu_us"])
                aggregate["sum_logical_reads"] += row["sum_logical_reads"]
                aggregate["max_logical_reads"] = max(aggregate["max_logical_reads"], row["max_logical_reads"])
                aggregate["sum_affected_rows"] += row["sum_affected_rows"]
                aggregate["max_affected_rows"] = max(aggregate["max_affected_rows"], row["max_affected_rows"])
                aggregate["sum_return_rows"] += row["sum_return_rows"]
                aggregate["has_table_scan"] = max(aggregate["has_table_scan"], row["has_table_scan"])
                aggregate["fail_count"] += row["fail_count"]

        for key, row in aggregates.items():
            tid = int(row["TENANT_ID"])
            metadata = preferred_metadata[_metadata_identity(row)]
            tenant_name = metadata["TENANT_NAME"]
            db_name = metadata["DB_NAME"]
            user_name = metadata["USER_NAME"]
            query_sql = metadata["QUERY_SQL"]
            active.add((row["SQL_ID"], tid))
            await _pool.execute_query(
                target_ds,
                """
                INSERT INTO sql_audit_stat
                  (datasource_id, tenant_id, tenant_name, sql_id, plan_hash,
                   db_name, user_name, bucket_start_us, executions, sum_elapsed_us, max_elapsed_us,
                   sum_cpu_us, max_cpu_us, sum_logical_reads, max_logical_reads,
                   sum_affected_rows, max_affected_rows, sum_return_rows, has_table_scan,
                   fail_count, collect_mode)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'watchlist')
                ON DUPLICATE KEY UPDATE
                  tenant_name=VALUES(tenant_name), db_name=VALUES(db_name), user_name=VALUES(user_name),
                  executions=VALUES(executions), sum_elapsed_us=VALUES(sum_elapsed_us),
                  max_elapsed_us=VALUES(max_elapsed_us), sum_cpu_us=VALUES(sum_cpu_us),
                  max_cpu_us=VALUES(max_cpu_us), collected_at=NOW(3), collect_mode='watchlist'
                """,
                params=[
                    source_ds.id, tid, tenant_name,
                    row["SQL_ID"], row["PLAN_HASH"] or 0, db_name, user_name,
                    window_start_us, row["executions"], row["sum_elapsed_us"], row["max_elapsed_us"],
                    row["sum_cpu_us"], row["max_cpu_us"], row["sum_logical_reads"], row["max_logical_reads"],
                    row["sum_affected_rows"], row["max_affected_rows"], row["sum_return_rows"],
                    int(bool(row["has_table_scan"])), row["fail_count"],
                ],
            )
            if query_sql:
                await _pool.execute_query(
                    target_ds,
                    """
                    INSERT INTO sql_audit_samples
                      (datasource_id, tenant_id, tenant_name, sql_id, plan_hash,
                       db_name, user_name, cause_type, request_time, elapsed_time, execute_time,
                       cpu_time, queue_time, logical_reads, physical_reads, affected_rows,
                       return_rows, table_scan, ret_code, query_sql)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,'watchlist',%s,%s,%s,%s,0,%s,0,%s,%s,%s,0,%s)
                    """,
                    params=[
                        source_ds.id, tid, tenant_name,
                        row["SQL_ID"], row["PLAN_HASH"] or 0, db_name, user_name,
                        window_end_us, row["max_elapsed_us"], row["max_cpu_us"], row["max_cpu_us"],
                        row["max_logical_reads"], row["max_affected_rows"], row["sum_return_rows"],
                        int(bool(row["has_table_scan"])), query_sql,
                    ],
                )

    active_sql_ids = {sid for sid, _ in active}
    # Update watchlist per tenant
    for tid in tenant_ids:
        tid_active = [(sid, t) for sid, t in active if t == tid]
        tid_sql_ids = await watchlist.get_active_ids(target_ds, source_ds.id, tid)
        idle_ids = [sid for sid in tid_sql_ids if sid not in active_sql_ids]
        if idle_ids:
            await watchlist.increment_idle(target_ds, source_ds.id, tid, idle_ids)
        if tid_active:
            await watchlist.add_or_renew(target_ds, source_ds.id, tid, [sid for sid, _ in tid_active], "watchlist")

    logger.info("watchlist_collect window=%s-%s tracked=%s active=%s",
                window_start_us, window_end_us, len(all_sql_ids), len(active))


async def collect_samples(source_ds: DataSource, target_ds: DataSource, last_request_time_us: int) -> tuple[int, list[tuple[str, int]]]:
    """Cursor-based incremental sample collection by cause_type. Returns (max_ts, [(sql_id, tenant_id)])."""
    result = await _pool.execute_query(
        source_ds,
        f"""
        SELECT SQL_ID, TENANT_ID, TENANT_NAME, DB_NAME, USER_NAME, PLAN_HASH,
               REQUEST_TIME, ELAPSED_TIME, EXECUTE_TIME, QUEUE_TIME,
               MEMSTORE_READ_ROW_COUNT, SSSTORE_READ_ROW_COUNT, DISK_READS,
               AFFECTED_ROWS, RETURN_ROWS, TABLE_SCAN, RET_CODE,
               SUBSTRING(QUERY_SQL, 1, 4096) AS QUERY_SQL
        FROM oceanbase.GV$OB_SQL_AUDIT
        WHERE REQUEST_TIME > %s
          AND DB_NAME NOT IN ({_EXCLUDED_PLACEHOLDER})
          AND IS_INNER_SQL = 0
          AND (
              ELAPSED_TIME >= %s
              OR (MEMSTORE_READ_ROW_COUNT + SSSTORE_READ_ROW_COUNT) >= %s
              OR RET_CODE != 0
              OR (TABLE_SCAN = 1 AND ELAPSED_TIME >= %s)
          )
        ORDER BY REQUEST_TIME ASC
        LIMIT 500
        """,
        params=[
            last_request_time_us, *_EXCLUDED_DBS,
            cfg.slow_sql_threshold_us, cfg.large_query_rows, cfg.table_scan_slow_threshold_us,
        ],
    )

    if result["row_count"] == 0:
        return last_request_time_us, []

    cols = result["columns"]
    raw = result["rows"]
    rows = [r if isinstance(r, dict) else dict(zip(cols, r)) for r in raw]
    preferred_metadata = await _enrich_preferred_metadata(
        source_ds,
        _build_preferred_metadata(rows),
        end_time_us=max((row["REQUEST_TIME"] for row in rows), default=last_request_time_us) + 1,
    )
    triggered: set[tuple[str, int]] = set()
    max_request_time = last_request_time_us

    for row in rows:
        max_request_time = max(max_request_time, row["REQUEST_TIME"])
        metadata = preferred_metadata[_metadata_identity(row)]
        logical_reads = row["MEMSTORE_READ_ROW_COUNT"] + row["SSSTORE_READ_ROW_COUNT"]
        causes = [ct for ct, cond in _CAUSE_TYPE_CONDITIONS if cond(row)]
        for cause_type in causes:
            await _pool.execute_query(
                target_ds,
                """
                INSERT INTO sql_audit_samples
                  (datasource_id, tenant_id, tenant_name, sql_id, plan_hash,
                   db_name, user_name, cause_type, request_time, elapsed_time, execute_time,
                   cpu_time, queue_time, logical_reads, physical_reads, affected_rows,
                   return_rows, table_scan, ret_code, query_sql)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                params=[
                    source_ds.id, row["TENANT_ID"], metadata["TENANT_NAME"],
                    row["SQL_ID"], row["PLAN_HASH"] or 0, metadata["DB_NAME"], metadata["USER_NAME"],
                    cause_type, row["REQUEST_TIME"], row["ELAPSED_TIME"], row["EXECUTE_TIME"],
                    row["EXECUTE_TIME"], row["QUEUE_TIME"], logical_reads, row["DISK_READS"],
                    row["AFFECTED_ROWS"], row["RETURN_ROWS"], int(bool(row["TABLE_SCAN"])),
                    row["RET_CODE"], metadata["QUERY_SQL"],
                ],
            )
            triggered.add((row["SQL_ID"], int(row["TENANT_ID"])))

    logger.info("sample_collect rows=%s triggered=%s max_request_time=%s",
                len(rows), len(triggered), max_request_time)
    return max_request_time, list(triggered)


async def upsert_sql_texts(
    target_ds: DataSource,
    datasource_id: int,
    sql_text_map: dict[str, str],
) -> int:
    """Batch INSERT IGNORE into sql_text_store. Returns count of rows attempted."""
    items = [(sql_id, text) for sql_id, text in sql_text_map.items() if text and text.strip()]
    if not items:
        return 0
    for sql_id, query_sql in items:
        await _pool.execute_query(
            target_ds,
            "INSERT IGNORE INTO sql_text_store (datasource_id, sql_id, query_sql) VALUES (%s, %s, %s)",
            params=[datasource_id, sql_id, query_sql],
        )
    return len(items)


async def run_sql_audit_collection(source_ds: DataSource, target_ds: DataSource) -> dict:
    from app.services.collector.plan_cache import collect_plans_for_sql_ids

    cp = await checkpoint.get_or_create(target_ds, f"sql_audit_{source_ds.id}")
    await checkpoint.update(target_ds, f"sql_audit_{source_ds.id}", status="running")

    now_us = _now_us()
    window_end_us = now_us - 1_000_000  # 1s buffer to avoid uncommitted rows
    window_start_us = window_end_us - cfg.collector_interval_seconds * 1_000_000
    last_sample_ts = int(cp.get("last_value") or 0)
    if last_sample_ts == 0:
        last_sample_ts = now_us - 15 * 60 * 1_000_000  # first run: start from 15 min ago

    try:
        threshold_pairs = await collect_threshold(source_ds, target_ds, window_start_us, window_end_us)

        # Derive tenant_ids from actual collected data for watchlist
        seen_tenants: set[int] = set()
        if threshold_pairs:
            by_tenant: dict[int, list[str]] = {}
            for sql_id, tid in threshold_pairs:
                seen_tenants.add(tid)
                by_tenant.setdefault(tid, []).append(sql_id)
            for tid, sids in by_tenant.items():
                await watchlist.add_or_renew(target_ds, source_ds.id, tid, sids, "threshold")

        if seen_tenants:
            await collect_watchlist(source_ds, target_ds, seen_tenants, window_start_us, window_end_us)

        new_last_ts, sample_pairs = await collect_samples(source_ds, target_ds, last_sample_ts)
        if sample_pairs:
            by_tenant_s: dict[int, list[str]] = {}
            for sql_id, tid in sample_pairs:
                seen_tenants.add(tid)
                by_tenant_s.setdefault(tid, []).append(sql_id)
            for tid, sids in by_tenant_s.items():
                await watchlist.add_or_renew(target_ds, source_ds.id, tid, sids, "sample")

        await watchlist.evict_idle(target_ds, source_ds.id)
        await watchlist.enforce_cap(target_ds, source_ds.id)

        # Collect sql_text_map from all three collection phases via sql_audit_samples
        # (samples already have query_sql written; read back to build the map for upsert)
        all_sql_ids: set[str] = {sid for sid, _ in threshold_pairs} | {sid for sid, _ in sample_pairs}

        sql_text_map: dict[str, str] = {}
        if all_sql_ids:
            # Build from preferred_metadata already fetched during threshold collection.
            # Re-query sql_audit_samples for any sql_id that has a query_sql written there.
            placeholders = ", ".join(["%s"] * len(all_sql_ids))
            try:
                text_result = await _pool.execute_query(
                    target_ds,
                    f"""
                    SELECT sql_id, MAX(query_sql) AS query_sql
                    FROM sql_audit_samples
                    WHERE datasource_id = %s AND sql_id IN ({placeholders})
                      AND query_sql IS NOT NULL AND query_sql != ''
                    GROUP BY sql_id
                    """,
                    params=[source_ds.id, *all_sql_ids],
                )
                for r in text_result["rows"]:
                    rd = r if isinstance(r, dict) else dict(zip(text_result["columns"], r))
                    if rd.get("query_sql"):
                        sql_text_map[rd["sql_id"]] = rd["query_sql"]
            except Exception as e:
                logger.warning("sql_text_map_build_failed: %s", e)

            # For sql_ids still missing text, fall back to a targeted GV$OB_SQL_AUDIT lookup
            missing_ids = [sid for sid in all_sql_ids if sid not in sql_text_map]
            if missing_ids:
                miss_ph = ", ".join(["%s"] * len(missing_ids))
                try:
                    miss_result = await _pool.execute_query(
                        source_ds,
                        f"""
                        SELECT SQL_ID, SUBSTRING(QUERY_SQL, 1, 4096) AS QUERY_SQL
                        FROM oceanbase.GV$OB_SQL_AUDIT
                        WHERE SQL_ID IN ({miss_ph})
                          AND COALESCE(TRIM(QUERY_SQL), '') != ''
                        ORDER BY REQUEST_TIME DESC
                        LIMIT 500
                        """,
                        params=missing_ids,
                    )
                    for r in miss_result["rows"]:
                        rd = r if isinstance(r, dict) else dict(zip(miss_result["columns"], r))
                        sid = rd.get("SQL_ID")
                        text = rd.get("QUERY_SQL")
                        if sid and text and sid not in sql_text_map:
                            sql_text_map[sid] = text
                except Exception as e:
                    logger.warning("sql_text_fallback_failed: %s", e)

        sql_texts_written = await upsert_sql_texts(target_ds, source_ds.id, sql_text_map)

        new_plans = 0
        if all_sql_ids:
            new_plans = await collect_plans_for_sql_ids(source_ds, target_ds, list(all_sql_ids))

        await checkpoint.update(
            target_ds, f"sql_audit_{source_ds.id}",
            last_value=new_last_ts, row_count=len(threshold_pairs), status="idle",
        )
        return {
            "status": "ok",
            "threshold_rows": len(threshold_pairs),
            "sample_sql_ids": len(sample_pairs),
            "sql_texts_written": sql_texts_written,
            "new_plans": new_plans,
        }
    except Exception as e:
        await checkpoint.update(
            target_ds, f"sql_audit_{source_ds.id}", status="error", error_msg=str(e)
        )
        raise
