---
name: pg-slow-query-triage
version: 1.0.0
description: PostgreSQL slow query diagnosis — identify top slow queries, analyze execution plans, and provide optimization recommendations
database: postgresql
always_apply: false
source: built_in
---
# PostgreSQL Slow Query Triage

## Goal
Identify and diagnose the most impactful slow queries, provide root cause and actionable fix.

## Workflow

### Step 1: Identify Top Slow Queries
```sql
SELECT queryid, query, calls, mean_exec_time, total_exec_time,
       rows, shared_blks_hit, shared_blks_read
FROM pg_stat_statements
ORDER BY mean_exec_time DESC
LIMIT 10;
```
Interpret: Focus on queries with high `mean_exec_time` AND high `calls` (frequent + slow = biggest impact).

### Step 2: Analyze Execution Plan
For the top suspect query:
```sql
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) <query>;
```
Look for:
- Seq Scan on large tables (missing index)
- Nested Loop with high row estimates
- Sort with `external merge` (work_mem too small)
- Buffers: high `read` vs `hit` (cold cache or bloated table)

### Step 3: Check Table Statistics
```sql
SELECT relname, seq_scan, idx_scan, n_live_tup, n_dead_tup, last_analyze
FROM pg_stat_user_tables
WHERE relname = '<table_name>';
```
If `last_analyze` is old or `n_dead_tup` is high, suggest `ANALYZE` or `VACUUM`.

### Step 4: Recommend Fix
Based on findings:
- Missing index → suggest `CREATE INDEX CONCURRENTLY`
- Stale statistics → `ANALYZE <table>`
- Table bloat → `VACUUM FULL` or `pg_repack`
- Bad join order → check `join_collapse_limit`, consider query rewrite
- work_mem too small → suggest session-level increase for specific queries

## Rules
- Always use `EXPLAIN ANALYZE` (not just `EXPLAIN`) to get actual execution data.
- Use `CONCURRENTLY` for index creation on production tables.
- Before recommending parameter changes, show current value and proposed value with justification.
