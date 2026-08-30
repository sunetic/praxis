# Praxis Evals

This directory contains the versioned Eval runners, fixtures, and public case corpus. The documentation site is the source of truth for the boundary between regular tests and live-model Evals, as well as execution, scoring, and reporting guidance:

- [Evaluation](../docs/reliability/evaluation.md)
- [PostgreSQL DBA Cases](../docs/reliability/pg-dba-eval.md)
- [MySQL DBA Cases](../docs/reliability/mysql-dba-eval.md)

The DBA suites follow a shared-core architecture:

- `dba_core/` owns engine-neutral catalog loading, scoring, reporting, model comparison, and real-service orchestration.
- `pg_dba/` and `mysql_dba/` contain only engine-specific suite metadata, database fixtures, session anomalies, SQL workloads, cases, and knowledge material.

Run the PostgreSQL suite with `make eval`, the MySQL suite with `make eval EVAL_SUITE=mysql`, or the fixed-harness model comparison with `make eval EVAL_PROFILE=model`. API keys must remain in local settings or environment variables and must never appear in cases, reports, or commits.
