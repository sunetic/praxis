---
name: mysql-connection-diagnosis
version: 1.0.0
description: MySQL connection and thread diagnosis — detect exhaustion, sleeping connections, and thread contention
database: mysql
always_apply: false
source: built_in
---
# MySQL Connection & Thread Diagnosis

## Goal
Diagnose connection-related issues: thread exhaustion, sleeping connections, and thread pool contention.

## Workflow

### Step 1: Connection Overview
```sql
SELECT
    MAX(CASE WHEN VARIABLE_NAME = 'Threads_connected' THEN VARIABLE_VALUE END) + 0 AS threads_connected,
    MAX(CASE WHEN VARIABLE_NAME = 'Threads_running' THEN VARIABLE_VALUE END) + 0 AS threads_running,
    MAX(CASE WHEN VARIABLE_NAME = 'Max_used_connections' THEN VARIABLE_VALUE END) + 0 AS max_used_connections,
    @@max_connections AS max_connections,
    ROUND(
        100 * MAX(CASE WHEN VARIABLE_NAME = 'Threads_connected' THEN VARIABLE_VALUE END)
        / @@max_connections,
        1
    ) AS usage_pct
FROM performance_schema.global_status
WHERE VARIABLE_NAME IN ('Threads_connected', 'Threads_running', 'Max_used_connections');
```
Alert if `usage_pct` > 80%. `threads_running` > CPU cores indicates overload.

### Step 2: Connections by User/Host
```sql
SELECT user, host, db, command, count(*) AS count,
       SUM(IF(command = 'Sleep', 1, 0)) AS sleeping
FROM information_schema.processlist
GROUP BY user, host, db, command
ORDER BY count DESC
LIMIT 15;
```

### Step 3: Long-Running Queries
```sql
SELECT id, user, host, db, command, time AS seconds,
       SUBSTRING(info, 1, 200) AS query_preview
FROM information_schema.processlist
WHERE command != 'Sleep' AND time > 10
ORDER BY time DESC
LIMIT 10;
```

### Step 4: Sleeping Connections
```sql
SELECT id, user, host, db, time AS sleep_seconds
FROM information_schema.processlist
WHERE command = 'Sleep' AND time > 300
ORDER BY time DESC
LIMIT 15;
```
Connections sleeping > 5 minutes are candidates for cleanup.

### Step 5: Connection Error History
```sql
SELECT * FROM performance_schema.host_cache
WHERE SUM_CONNECT_ERRORS > 0
ORDER BY SUM_CONNECT_ERRORS DESC
LIMIT 10;
```

### Step 6: Recommend Action
- Near max_connections → increase or implement ProxySQL/connection pooling
- Many sleeping connections → lower `wait_timeout` (default 28800s is too high)
- Specific host hammering connections → check for connection leak in that app
- `Threads_running` >> CPU cores → queries are piling up; check for lock contention or missing indexes
- Connection errors from specific hosts → check `max_connect_errors`, firewall, or DNS

## Rules
- Use the canonical identifiers above exactly. Do not mutate a status or variable name while repairing SQL syntax.
- Status rows can change while a query scans them under active load, so the returned
  values are not guaranteed to be one atomic snapshot. If `Threads_running` is
  greater than `Threads_connected`, report sampling skew and corroborate with the
  process list or monitoring series; do not infer impossible connection arithmetic.
- Being below `max_connections` shows current headroom, but does not prove that no
  connections were rejected. Check `Aborted_connects` over the relevant window
  before making an absence claim.
- `wait_timeout` should typically be 300-600s for web applications, not the default 8 hours.
- Prefer connection pooling (ProxySQL) over raising `max_connections` past 500.
- `KILL <id>` only with user confirmation.
