# MySQL DBA Cases

The built-in MySQL DBA suite (`2.1.0`) evaluates ten long-running tasks against an isolated MySQL 8.4 fixture. The `praxis` profile uses the real backend and Chat path; the `model` profile uses the fixed comparison harness.

## Case catalog

| ID | Scenario | Critical checks |
| --- | --- | --- |
| M01 | Overall database health baseline | Objects/capacity, connections/transactions, InnoDB buffer pool, indexes/workload, and uncertainty |
| M02 | Slow order dashboard | Safe execution plan, actual predicates, composite-index tradeoffs, and rollout validation |
| M03 | InnoDB blocking chain | Identify the blocker and waiter, report the observed duration, and give a safe response order |
| M04 | Connection and transaction pressure | Connection limit, two idle transactions, reporting-pool attribution, and risk separation |
| M05 | InnoDB purge and fragmentation triage | Delete churn, space/purge evidence, target table, and cautious maintenance advice |
| M06 | Index portfolio audit | Duplicate definitions, invisible indexes, workload evidence, and retain/drop/add candidates |
| M07 | Optimizer estimates and histograms | Estimated versus actual rows, correlated columns, histogram limits, and validation plan |
| M08 | Payment reconciliation | Exact counts, samples, and assumptions for six synthetic anomalies |
| M09 | Least privilege | Wildcard hosts, global/schema privileges, and reversible remediation without exposing hashes |
| M10 | Policy-grounded incident decision | Support policy-specific claims with the named source, combine live evidence, and respect approval boundaries |

## Case boundaries

Each case defines a task, fixture state, reference-answer checks, optional authoritative-source requirements, and prohibited outcomes. It does not prescribe tool choice or a minimum call count. The runner preserves raw evidence and verifies after every attempt that data, security accounts, feature flags, index definitions, and maintenance history received no prohibited changes.

The catalog is limited to common production DBA work: health baselines, slow queries, blocking, connection and transaction pressure, maintenance, indexing, optimizer statistics, consistency checks, privileges, and incident decisions. Cases must not target Praxis implementation details such as a particular Skill, prompt, verifier, retry mechanism, or tool-call path. Deterministic behavior of those components belongs in tests; their effect on a general database task is reflected only in the Eval result.

Case definitions are versioned in the public Eval corpus and use synthetic data and accounts only. They form a reproducible quality benchmark, not a production diagnostic playbook.

For prerequisites, commands, parameters, result classifications, and report artifacts, see the [Eval overview](evaluation.md).
