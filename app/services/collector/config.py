import os


class CollectorConfig:
    # SQL Audit 采集
    collector_interval_seconds: int = int(os.getenv("COLLECTOR_INTERVAL_SECONDS", "30"))
    cpu_threshold_us: int = int(os.getenv("CPU_THRESHOLD_US", "500000"))
    slow_sql_threshold_us: int = int(os.getenv("SLOW_SQL_THRESHOLD_US", "1000000"))
    large_query_rows: int = int(os.getenv("LARGE_QUERY_ROWS", "100000"))
    table_scan_slow_threshold_us: int = int(os.getenv("TABLE_SCAN_SLOW_THRESHOLD_US", "500000"))

    # Watchlist
    watchlist_idle_windows: int = int(os.getenv("WATCHLIST_IDLE_WINDOWS", "60"))
    max_watchlist_size: int = int(os.getenv("MAX_WATCHLIST_SIZE", "5000"))
    watchlist_batch_size: int = int(os.getenv("WATCHLIST_BATCH_SIZE", "200"))

    # Plan Cache 采集
    plan_cache_interval_seconds: int = int(os.getenv("PLAN_CACHE_INTERVAL_SECONDS", "300"))
    max_plan_cache_rows: int = int(os.getenv("MAX_PLAN_CACHE_ROWS", "5000"))

    # 数据保留
    sql_audit_stat_retention_days: int = int(os.getenv("SQL_AUDIT_STAT_RETENTION_DAYS", "14"))
    sql_audit_sample_retention_days: int = int(os.getenv("SQL_AUDIT_SAMPLE_RETENTION_DAYS", "30"))
    plan_cache_retention_days: int = int(os.getenv("PLAN_CACHE_RETENTION_DAYS", "30"))


cfg = CollectorConfig()
