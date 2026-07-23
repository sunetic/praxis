---
name: pg-connection-diagnosis
version: 1.0.0
description: PostgreSQL connection and session diagnosis — detect exhaustion, idle connections, and abnormal wait events
database: postgresql
always_apply: false
source: built_in
---
# PostgreSQL Connection & Session Diagnosis

## Goal
Diagnose connection-related issues: pool exhaustion, idle connection bloat, abnormal session states.

## Workflow

### Step 1: Connection Overview
```sql
SELECT
    count(*) AS total,
    count(*) FILTER (WHERE state = 'active') AS active,
    count(*) FILTER (WHERE state = 'idle') AS idle,
    count(*) FILTER (WHERE state = 'idle in transaction') AS idle_in_txn,
    count(*) FILTER (WHERE state = 'idle in transaction (aborted)') AS idle_in_txn_aborted,
    current_setting('max_connections')::int AS max_connections
FROM pg_stat_activity
WHERE backend_type = 'client backend';
```
Alert if `total` > 80% of `max_connections`.
Alert if `idle_in_txn` > 10 — these hold locks and consume resources.

### Step 2: Connections by Application/User
```sql
SELECT usename, application_name, client_addr, state, count(*)
FROM pg_stat_activity
WHERE backend_type = 'client backend'
GROUP BY usename, application_name, client_addr, state
ORDER BY count(*) DESC
LIMIT 15;
```
Identify which application is consuming the most connections.

### Step 3: Top Wait Events
```sql
SELECT wait_event_type, wait_event, count(*)
FROM pg_stat_activity
WHERE state = 'active' AND wait_event IS NOT NULL
GROUP BY wait_event_type, wait_event
ORDER BY count(*) DESC
LIMIT 10;
```
Common concerning waits:
- `Lock` / `transactionid` → lock contention (switch to lock diagnosis skill)
- `IO` / `DataFileRead` → disk I/O bottleneck
- `LWLock` / `buffer_mapping` → buffer pool contention

### Step 4: Long-Idle Sessions
```sql
SELECT pid, usename, application_name, state,
       now() - state_change AS idle_duration,
       query
FROM pg_stat_activity
WHERE state IN ('idle', 'idle in transaction')
  AND now() - state_change > interval '30 minutes'
ORDER BY state_change ASC
LIMIT 10;
```

### Step 5: Recommend Action
- Connection exhaustion → increase `max_connections` (with caution) or implement connection pooling (PgBouncer)
- Idle in transaction → set `idle_in_transaction_session_timeout` to auto-terminate
- Too many idle connections → configure pooler with appropriate pool_size
- Specific app hogging connections → work with app team to reduce pool size or fix connection leak

## Rules
- Increasing `max_connections` has diminishing returns and increases per-connection memory. Pooling is the real fix.
- `idle in transaction` sessions are more harmful than `idle` — they hold locks.
- Always show which application is responsible before recommending a fix.
