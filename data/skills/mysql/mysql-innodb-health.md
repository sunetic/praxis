---
name: mysql-innodb-health
version: 1.0.0
description: MySQL InnoDB engine health check — buffer pool hit ratio, disk I/O, redo log, and adaptive hash index diagnostics
database: mysql
always_apply: false
source: built_in
---
# MySQL InnoDB Health Check

## Goal
Assess InnoDB engine health: buffer pool efficiency, disk I/O patterns, redo log pressure, and internal contention.

## Workflow

### Step 1: Buffer Pool Hit Ratio
```sql
SELECT
    VARIABLE_NAME, VARIABLE_VALUE
FROM performance_schema.global_status
WHERE VARIABLE_NAME IN (
    'Innodb_buffer_pool_read_requests',
    'Innodb_buffer_pool_reads',
    'Innodb_buffer_pool_pages_total',
    'Innodb_buffer_pool_pages_free',
    'Innodb_buffer_pool_pages_dirty'
);
```
Calculate hit ratio:
```
hit_ratio = (read_requests - reads) / read_requests * 100
```
Healthy: > 99%. Below 95% indicates buffer pool is too small or workload exceeds memory.

### Step 2: Buffer Pool Size vs Data Size
```sql
SELECT
    ROUND(SUM(DATA_LENGTH + INDEX_LENGTH) / 1024 / 1024, 0) AS total_data_mb,
    @@innodb_buffer_pool_size / 1024 / 1024 AS buffer_pool_mb
FROM information_schema.tables
WHERE TABLE_SCHEMA NOT IN ('mysql', 'information_schema', 'performance_schema', 'sys');
```
Rule of thumb: buffer pool should be 60-80% of total server RAM and ideally >= data size.

### Step 3: I/O Throughput
```sql
SELECT
    VARIABLE_NAME, VARIABLE_VALUE
FROM performance_schema.global_status
WHERE VARIABLE_NAME IN (
    'Innodb_data_reads', 'Innodb_data_writes',
    'Innodb_data_read', 'Innodb_data_written',
    'Innodb_os_log_written',
    'Innodb_log_waits'
);
```
`Innodb_log_waits` > 0 means redo log is too small — transactions had to wait for log space.

### Step 4: Row Operations Profile
```sql
SELECT
    VARIABLE_NAME, VARIABLE_VALUE
FROM performance_schema.global_status
WHERE VARIABLE_NAME IN (
    'Innodb_rows_read', 'Innodb_rows_inserted',
    'Innodb_rows_updated', 'Innodb_rows_deleted'
);
```
High `rows_read` vs `rows_inserted/updated/deleted` ratio indicates read-heavy workload.

### Step 5: Table Space and Fragmentation
```sql
SELECT TABLE_SCHEMA, TABLE_NAME,
       ROUND(DATA_LENGTH / 1024 / 1024, 1) AS data_mb,
       ROUND(INDEX_LENGTH / 1024 / 1024, 1) AS index_mb,
       ROUND(DATA_FREE / 1024 / 1024, 1) AS free_mb,
       TABLE_ROWS
FROM information_schema.tables
WHERE TABLE_SCHEMA = DATABASE()
  AND DATA_FREE > 100 * 1024 * 1024
ORDER BY DATA_FREE DESC
LIMIT 10;
```
High `DATA_FREE` indicates fragmentation — `OPTIMIZE TABLE` may reclaim space.

### Step 6: Recommend Action
- Low hit ratio → increase `innodb_buffer_pool_size`
- `Innodb_log_waits` > 0 → increase `innodb_redo_log_capacity` (8.0.30+) or `innodb_log_file_size`
- High fragmentation → `ALTER TABLE <table> ENGINE=InnoDB` to rebuild (online DDL)
- Dirty pages > 75% of pool → check `innodb_io_capacity` and disk throughput

## Rules
- Buffer pool resize is online in MySQL 8.0 (`SET GLOBAL innodb_buffer_pool_size = ...`) but may cause brief stall.
- `OPTIMIZE TABLE` rebuilds the table — check table size and available disk before recommending.
- Always compare metrics over time, not just snapshot values.
