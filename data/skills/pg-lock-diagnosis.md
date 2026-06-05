---
name: pg-lock-diagnosis
version: 1.0.0
description: PostgreSQL lock contention diagnosis — find blocker chains, long-running transactions, and deadlock sources
database: postgresql
always_apply: false
source: built_in
---
# PostgreSQL Lock Contention Diagnosis

## Goal
Identify blocking sessions, lock wait chains, and long-running transactions that cause contention.

## Workflow

### Step 1: Find Blocking Chains
```sql
SELECT
    blocked.pid AS blocked_pid,
    blocked.usename AS blocked_user,
    blocked.query AS blocked_query,
    blocked.wait_event_type,
    blocking.pid AS blocking_pid,
    blocking.usename AS blocking_user,
    blocking.query AS blocking_query,
    blocking.state AS blocking_state,
    now() - blocking.xact_start AS blocking_duration
FROM pg_stat_activity blocked
JOIN pg_locks bl ON bl.pid = blocked.pid AND NOT bl.granted
JOIN pg_locks gl ON gl.locktype = bl.locktype
    AND gl.database IS NOT DISTINCT FROM bl.database
    AND gl.relation IS NOT DISTINCT FROM bl.relation
    AND gl.page IS NOT DISTINCT FROM bl.page
    AND gl.tuple IS NOT DISTINCT FROM bl.tuple
    AND gl.pid != bl.pid
    AND gl.granted
JOIN pg_stat_activity blocking ON blocking.pid = gl.pid
ORDER BY blocking_duration DESC;
```
Interpret: If `blocking_state` is `idle in transaction`, the blocker is holding locks without doing work.

### Step 2: Long-Running Transactions
```sql
SELECT pid, usename, state, query,
       now() - xact_start AS xact_duration,
       now() - query_start AS query_duration,
       wait_event_type, wait_event
FROM pg_stat_activity
WHERE state != 'idle'
  AND xact_start IS NOT NULL
ORDER BY xact_duration DESC
LIMIT 10;
```
Flag any transaction running longer than 5 minutes.

### Step 3: Lock Type Summary
```sql
SELECT locktype, mode, COUNT(*) AS count,
       COUNT(*) FILTER (WHERE granted) AS granted,
       COUNT(*) FILTER (WHERE NOT granted) AS waiting
FROM pg_locks
GROUP BY locktype, mode
ORDER BY waiting DESC, count DESC;
```

### Step 4: Recommend Action
- `idle in transaction` blocker → ask user if safe to `pg_terminate_backend(pid)`
- Long DDL holding `AccessExclusiveLock` → check if it can be cancelled
- Frequent contention on same table → suggest query optimization or partitioning
- Application-level: suggest adding `SET lock_timeout = '5s'` for DDL operations

## Rules
- Never terminate a backend without user confirmation.
- Always show the blocking query content so the user can assess impact.
- If deadlocks are suspected, check `pg_stat_database.deadlocks` counter.
