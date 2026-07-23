---
name: pg-vacuum-health
version: 1.0.0
description: PostgreSQL vacuum and bloat health check — detect bloated tables, stale vacuum, and autovacuum issues
database: postgresql
always_apply: false
source: built_in
---
# PostgreSQL Vacuum & Bloat Health Check

## Goal
Assess vacuum health, identify bloated tables, and detect autovacuum problems before they cause performance degradation or transaction ID wraparound.

## Workflow

### Step 1: Tables Needing Vacuum Most
```sql
SELECT schemaname, relname,
       n_live_tup, n_dead_tup,
       ROUND(n_dead_tup::numeric / NULLIF(n_live_tup + n_dead_tup, 0) * 100, 1) AS dead_pct,
       last_vacuum, last_autovacuum, last_analyze, last_autoanalyze,
       vacuum_count, autovacuum_count
FROM pg_stat_user_tables
WHERE n_dead_tup > 1000
ORDER BY n_dead_tup DESC
LIMIT 15;
```
Interpret: `dead_pct` > 20% indicates significant bloat. Tables with no recent vacuum/analyze need attention.

### Step 2: Table Size vs Estimated Live Data
```sql
SELECT c.relname,
       pg_total_relation_size(c.oid) AS total_bytes,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size,
       c.reltuples::bigint AS est_rows,
       pg_size_pretty((c.reltuples * avg_width)::bigint) AS est_data_size
FROM pg_class c
JOIN pg_stats s ON s.tablename = c.relname
WHERE c.relkind = 'r' AND c.relnamespace = 'public'::regnamespace
GROUP BY c.oid, c.relname, c.reltuples
HAVING pg_total_relation_size(c.oid) > 100 * 1024 * 1024
ORDER BY pg_total_relation_size(c.oid) DESC
LIMIT 10;
```
If `total_size` >> `est_data_size`, the table is bloated.

### Step 3: Autovacuum Status
```sql
-- Currently running autovacuum workers
SELECT pid, datname, relid::regclass AS table_name, phase,
       heap_blks_total, heap_blks_scanned, heap_blks_vacuumed
FROM pg_stat_progress_vacuum;
```
```sql
-- Autovacuum settings
SHOW autovacuum;
SHOW autovacuum_vacuum_threshold;
SHOW autovacuum_vacuum_scale_factor;
SHOW autovacuum_max_workers;
```

### Step 4: Transaction ID Wraparound Risk
```sql
SELECT datname,
       age(datfrozenxid) AS xid_age,
       current_setting('autovacuum_freeze_max_age')::bigint AS freeze_max_age,
       ROUND(age(datfrozenxid)::numeric /
             current_setting('autovacuum_freeze_max_age')::numeric * 100, 1) AS pct_towards_wraparound
FROM pg_database
WHERE datallowconn
ORDER BY xid_age DESC;
```
Alert if `pct_towards_wraparound` > 50%.

### Step 5: Recommend Action
- High dead_pct → `VACUUM ANALYZE <table>` (online, non-blocking)
- Severe bloat → `VACUUM FULL <table>` (blocking! confirm with user) or `pg_repack`
- Autovacuum lagging → increase `autovacuum_max_workers` or lower `autovacuum_vacuum_scale_factor`
- Approaching wraparound → emergency manual `VACUUM FREEZE`

## Rules
- `VACUUM FULL` acquires `AccessExclusiveLock` — always warn the user about downtime.
- Prefer `pg_repack` over `VACUUM FULL` for production tables.
- Run `ANALYZE` after any significant bulk data change.
