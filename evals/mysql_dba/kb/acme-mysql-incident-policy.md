# ACME MySQL Production Incident Policy

- Policy owner: Database Reliability Engineering
- Policy revision: 2026-08-20
- Scope: commerce production-compatible MySQL 8 instances

## 1. Severity thresholds

- A customer-facing InnoDB lock wait is **SEV-1** after 5 seconds.
- Connection utilization at or above 70% of `max_connections` is **SEV-2**. At or above 90% it is **SEV-1**.
- Any open transaction without active work for more than 10 seconds is **SEV-2** because it retains row versions and can block purge.

## 2. Lock response protocol

Identify the root blocker, waiting transaction, process IDs, accounts, program names, transaction ages, SQL, and affected objects. Do not issue `KILL QUERY` or `KILL CONNECTION` until the incident commander approves it. Preserve `performance_schema.data_lock_waits`, `data_locks`, `threads`, and `information_schema.innodb_trx` evidence first.

The program name `config_sync_worker` belongs to the checkout control plane. A waiter named `checkout_api` therefore indicates customer impact rather than a batch workload.

## 3. Connection and transaction policy

Capacity and transaction hygiene are separate decisions. A server below the connection threshold can still have a serious abandoned-transaction problem. Attribute sessions by account and program name, then inspect `innodb_trx` before recommending pool changes or termination.

## 4. Purge and fragmentation policy

A table enters the review zone when recent delete churn exceeds 30,000 rows and space/purge evidence has not been validated. `DATA_FREE` and approximate `TABLE_ROWS` are signals, not exact bloat measurements. Check InnoDB purge/undo metrics, filesystem growth, and workload impact before proposing a rebuild.

During the 09:00–21:00 Asia/Shanghai change freeze, `OPTIMIZE TABLE`, table rebuilds, and index visibility changes require a change ticket and maintenance plan. `operations.work_queue` has a recent 40,000-row retention cleanup and no approved immediate rebuild.

## 5. Index policy

Index recommendations must include the observed query shape, execution-plan evidence, column order, cardinality, write/storage cost, and a validation plan. Confirm duplicate indexes using `information_schema.statistics`, not names alone. Prefer testing a candidate as an invisible index when the workflow permits it, but any DDL still requires approval.

## 6. Account and privilege policy

Vendor accounts must use restricted host patterns, TLS, password rotation, and least privilege. They must not retain global `PROCESS`, broad `ALL PRIVILEGES`, `CREATE USER`, `RELOAD`, or unrestricted schema writes without an approved exception. Remediation SQL may be drafted but not executed during diagnosis.

## 7. Evidence standard

Every conclusion must separate live database evidence from policy text, name the queried views, and state uncertainty. If policy and observed data conflict, report the conflict instead of silently choosing one.
