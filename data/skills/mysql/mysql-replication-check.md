---
name: mysql-replication-check
version: 1.0.0
description: MySQL replication health check — monitor replica lag, GTID consistency, and replication errors
database: mysql
always_apply: false
source: built_in
---
# MySQL Replication Health Check

## Goal
Assess replication health, detect lag, and verify replica consistency.

## Workflow

### Step 1: Replica Status
```sql
SHOW REPLICA STATUS\G
```
Key fields to check:
- `Replica_IO_Running`: must be `Yes`
- `Replica_SQL_Running`: must be `Yes`
- `Seconds_Behind_Source`: replication delay in seconds
- `Last_IO_Error` / `Last_SQL_Error`: any error messages
- `Retrieved_Gtid_Set` / `Executed_Gtid_Set`: GTID progress

For MySQL < 8.0.22:
```sql
SHOW SLAVE STATUS\G
```

### Step 2: GTID Consistency
On source:
```sql
SELECT @@gtid_executed;
```
On replica:
```sql
SELECT @@gtid_executed;
```
Compare the two — replica's set should be a subset of (or equal to) source's.

### Step 3: Replication Lag Detail
```sql
-- Check if lag is from IO thread or SQL thread
SELECT
    CHANNEL_NAME,
    SERVICE_STATE,
    REMAINING_DELAY,
    COUNT_TRANSACTIONS_IN_QUEUE AS relay_log_queue
FROM performance_schema.replication_applier_status
JOIN performance_schema.replication_connection_status USING (CHANNEL_NAME);
```

### Step 4: Replication Filters
```sql
SHOW REPLICA STATUS\G
```
Check `Replicate_Do_DB`, `Replicate_Ignore_DB`, `Replicate_Do_Table`, `Replicate_Wild_Do_Table` — filters can silently skip important changes.

### Step 5: Recommend Action
- IO thread stopped → check network, source binlog purge, `CHANGE REPLICATION SOURCE TO` reconfigure
- SQL thread stopped → check `Last_SQL_Error`, likely schema conflict or duplicate key
- Persistent lag → check if replica hardware can keep up, consider parallel replication (`replica_parallel_workers`)
- GTID gap → check for errant transactions on replica
- High relay log queue → SQL thread bottleneck, consider `replica_parallel_type = LOGICAL_CLOCK`

## Rules
- `Seconds_Behind_Source` can be misleading — a replica catching up after a pause shows low lag but is still behind.
- Always check both IO and SQL thread status — they fail independently.
- Replication filters are dangerous in GTID mode — flag any non-empty filter for user review.
