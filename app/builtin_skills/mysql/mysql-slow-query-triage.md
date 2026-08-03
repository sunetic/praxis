---
name: mysql-slow-query-triage
version: 1.0.0
description: MySQL slow query diagnosis — identify top slow queries from performance_schema, analyze execution plans, and recommend optimizations
database: mysql
always_apply: false
source: built_in
---
# MySQL Slow Query Triage

## Goal
Identify and diagnose the most impactful slow queries, provide root cause and actionable fix.

## Workflow

### Step 1: Top Slow Queries by Total Time
```sql
SELECT DIGEST_TEXT, COUNT_STAR AS exec_count,
       ROUND(SUM_TIMER_WAIT / 1e12, 2) AS total_time_s,
       ROUND(AVG_TIMER_WAIT / 1e12, 4) AS avg_time_s,
       SUM_ROWS_EXAMINED, SUM_ROWS_SENT,
       ROUND(SUM_ROWS_EXAMINED / NULLIF(SUM_ROWS_SENT, 0), 0) AS exam_to_sent_ratio
FROM performance_schema.events_statements_summary_by_digest
WHERE SCHEMA_NAME = DATABASE()
ORDER BY SUM_TIMER_WAIT DESC
LIMIT 10;
```
Interpret: High `exam_to_sent_ratio` (>100) means the query examines far more rows than it returns — likely missing an index.

### Step 2: Full Table Scans
```sql
SELECT * FROM sys.statements_with_full_table_scans
ORDER BY no_index_used_count DESC
LIMIT 10;
```
Or if sys schema unavailable:
```sql
SELECT DIGEST_TEXT, COUNT_STAR, SUM_NO_INDEX_USED
FROM performance_schema.events_statements_summary_by_digest
WHERE SUM_NO_INDEX_USED > 0
ORDER BY SUM_NO_INDEX_USED DESC
LIMIT 10;
```

### Step 3: Execution Plan
```sql
EXPLAIN FORMAT=TREE <query>;
```
Or for older versions:
```sql
EXPLAIN <query>;
```
Look for:
- `type: ALL` (full table scan)
- `Using filesort` or `Using temporary`
- High `rows` estimate with no index
- Nested loop with unindexed inner table

### Step 4: Index Usage for Target Table
```sql
SHOW INDEX FROM <table_name>;
```
```sql
SELECT index_name, rows_selected, rows_inserted, rows_updated, rows_deleted
FROM sys.schema_index_statistics
WHERE table_schema = DATABASE() AND table_name = '<table_name>';
```

### Step 5: Recommend Fix
- Missing index → `ALTER TABLE <table> ADD INDEX idx_<col> (<col>)` (note: online DDL behavior varies by version)
- Stale statistics → `ANALYZE TABLE <table>`
- Filesort on large result → add covering index or rewrite query
- High exam-to-sent ratio with proper indexes → check if query can be rewritten with better selectivity

## Rules
- MySQL `EXPLAIN FORMAT=TREE` (8.0.18+) gives actual cost estimates — prefer it over traditional `EXPLAIN`.
- Online DDL: MySQL 8.0 supports `ALGORITHM=INPLACE` for most index additions, but verify before recommending for production.
- Always check if the slow query is from a known ORM pattern (N+1, missing eager load) before suggesting index fixes.
