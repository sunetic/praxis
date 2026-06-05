---
name: pg-replication-check
version: 1.0.0
description: PostgreSQL replication health check — monitor lag, slot status, and standby readiness
database: postgresql
always_apply: false
source: built_in
---
# PostgreSQL Replication Health Check

## Goal
Assess streaming replication health, detect lag, and verify standby readiness.

## Workflow

### Step 1: Replication Status (Primary)
```sql
SELECT client_addr, state, sync_state,
       sent_lsn, write_lsn, flush_lsn, replay_lsn,
       pg_wal_lsn_diff(sent_lsn, replay_lsn) AS replay_lag_bytes,
       pg_size_pretty(pg_wal_lsn_diff(sent_lsn, replay_lsn)) AS replay_lag_pretty,
       write_lag, flush_lag, replay_lag
FROM pg_stat_replication;
```
Interpret: `replay_lag_bytes` > 100MB or `replay_lag` > 10s warrants investigation.

### Step 2: Replication Slots
```sql
SELECT slot_name, slot_type, active,
       pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn) AS retained_bytes,
       pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS retained_pretty
FROM pg_replication_slots;
```
Inactive slots with large `retained_bytes` cause WAL accumulation and disk pressure.

### Step 3: WAL Generation Rate
```sql
SELECT pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), '0/0')) AS total_wal_generated;
```
```sql
-- Recent WAL stats
SELECT stats_reset,
       pg_size_pretty(wal_bytes) AS wal_bytes
FROM pg_stat_wal;
```

### Step 4: Standby Query (on Replica)
```sql
SELECT pg_is_in_recovery() AS is_standby,
       pg_last_wal_receive_lsn() AS receive_lsn,
       pg_last_wal_replay_lsn() AS replay_lsn,
       pg_last_xact_replay_timestamp() AS last_replay_ts,
       now() - pg_last_xact_replay_timestamp() AS replay_delay;
```

### Step 5: Recommend Action
- High replay lag → check standby I/O, `max_standby_streaming_delay`, `hot_standby_feedback`
- Inactive replication slot → drop if the consumer is permanently gone: `SELECT pg_drop_replication_slot('slot_name')`
- WAL accumulation → check `wal_keep_size` and slot retention policy
- Failover readiness → verify timeline consistency and `recovery_target_timeline = 'latest'`

## Rules
- Never drop a replication slot without user confirmation.
- Lag interpretation depends on workload — high-write systems tolerate more byte lag.
- Always check both primary and standby views for a complete picture.
