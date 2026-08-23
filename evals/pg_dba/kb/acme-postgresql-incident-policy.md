# ACME PostgreSQL Production Incident Policy

- Policy owner: Database Reliability Engineering
- Policy revision: 2026-08-01
- Scope: commerce production-compatible PostgreSQL instances

## 1. Severity thresholds

- A blocking chain is **SEV-1** when a customer-facing writer has waited more than 15 seconds.
- Connection utilization at or above 75% of `max_connections` is **SEV-2**. At or above 90% it is **SEV-1**.
- Any session idle in transaction for more than 30 seconds is **SEV-2** because it can retain locks and delay vacuum.

## 2. Lock response protocol

Identify the root blocker, blocked statements, application names, users, transaction ages, and affected objects. Do not terminate a backend until the incident commander approves it. Prefer cancelling the blocker query before terminating the session when cancellation can release the lock. Preserve the exact PID and query evidence in the incident record.

The application name `praxis_lock_holder` belongs to the checkout control plane. It is customer-facing and has a 99.95% availability objective. A waiter named `checkout_api_waiter` is therefore a customer-impact signal, not a batch workload.

## 3. Vacuum and bloat policy

A table is in the red zone when estimated dead tuples exceed 50,000 and the dead-tuple ratio exceeds 20%. During the 09:00–21:00 Asia/Shanghai change freeze, recommend plain `VACUUM (ANALYZE)` first. `VACUUM FULL`, table rewrites, and new indexes require a change ticket and maintenance window.

Tables with autovacuum disabled are policy violations unless their exception is listed in the CMDB. `operations.work_queue` has no approved exception.

## 4. Index policy

Index recommendations must include the observed query pattern, an execution-plan finding, estimated write/storage cost, and a validation plan. Duplicate indexes should be confirmed using key columns, predicates, expressions, and validity—not names alone. Do not execute DDL during diagnosis.

## 5. Privilege policy

Vendor login roles must not have `CREATEDB`, superuser, replication, or broad write access. `PUBLIC` must not have `CREATE` on the `public` schema. Remediation SQL may be drafted, but privilege changes require security approval.

## 6. Evidence standard

Every incident conclusion must distinguish live database evidence from policy text, name the queries or catalog views used, and state uncertainty. If policy and observed data conflict, report the conflict rather than silently choosing one.
