---
name: mysql-lock-diagnosis
version: 1.0.0
description: MySQL lock contention and deadlock diagnosis — find blocking transactions, InnoDB lock waits, and deadlock history
database: mysql
always_apply: false
source: built_in
---
# MySQL Lock Contention & Deadlock Diagnosis

## Goal
Identify blocking transactions, lock wait chains, and deadlock patterns in InnoDB.

## Workflow

### Step 1: Current Lock Waits
```sql
SELECT
    r.trx_id AS waiting_trx_id,
    r.trx_mysql_thread_id AS waiting_thread,
    r.trx_query AS waiting_query,
    r.trx_wait_started,
    TIMESTAMPDIFF(SECOND, r.trx_wait_started, NOW()) AS wait_seconds,
    b.trx_id AS blocking_trx_id,
    b.trx_mysql_thread_id AS blocking_thread,
    b.trx_query AS blocking_query,
    b.trx_started AS blocking_started
FROM information_schema.innodb_lock_waits w
JOIN information_schema.innodb_trx r ON r.trx_id = w.requesting_trx_id
JOIN information_schema.innodb_trx b ON b.trx_id = w.blocking_trx_id
ORDER BY wait_seconds DESC;
```
For MySQL 8.0+:
```sql
SELECT
    waiting_pid, waiting_query, waiting_lock_mode,
    blocking_pid, blocking_query, blocking_lock_mode
FROM sys.innodb_lock_waits;
```

### Step 2: Long-Running Transactions
```sql
SELECT trx_id, trx_state, trx_started,
       TIMESTAMPDIFF(SECOND, trx_started, NOW()) AS duration_s,
       trx_mysql_thread_id, trx_query, trx_rows_locked, trx_rows_modified
FROM information_schema.innodb_trx
ORDER BY trx_started ASC
LIMIT 10;
```
Flag any transaction held open > 60 seconds.

### Step 3: Data Lock Detail (MySQL 8.0+)
```sql
SELECT
    ENGINE_TRANSACTION_ID, OBJECT_SCHEMA, OBJECT_NAME,
    LOCK_TYPE, LOCK_MODE, LOCK_STATUS, LOCK_DATA
FROM performance_schema.data_locks
WHERE LOCK_STATUS = 'WAITING'
ORDER BY ENGINE_TRANSACTION_ID;
```

### Step 4: Last Deadlock Info
```sql
SHOW ENGINE INNODB STATUS\G
```
Parse the `LATEST DETECTED DEADLOCK` section. Key info:
- Which transactions were involved
- Which locks each held and waited for
- Which transaction was rolled back

### Step 5: Recommend Action
- Long idle transaction → ask user if safe to `KILL <thread_id>`
- Repeated deadlocks on same tables → suggest consistent lock ordering or batch size reduction
- Gap lock contention → consider `READ COMMITTED` isolation if applicable
- Metadata lock on DDL → check if long-running SELECT is blocking `ALTER TABLE`

## Rules
- Never KILL a thread without user confirmation.
- `sys.innodb_lock_waits` is preferred on MySQL 8.0+ — it gives human-readable output.
- Deadlock is normal at low frequency; only investigate if recurring or increasing.
