# PostgreSQL DBA Cases

The built-in PostgreSQL DBA suite (`2.1.0`) evaluates ten long-running tasks against an isolated PostgreSQL 16 fixture. The `praxis` profile uses the real backend and Chat path; the `model` profile uses the fixed comparison harness.

## Case catalog

| ID | Scenario | Critical checks |
| --- | --- | --- |
| C01 | Overall database health baseline | Capacity, connections/transactions, indexes, Vacuum, and uncertainty |
| C02 | Slow-query diagnosis | Safe execution plan, real predicates, index tradeoffs, and validation plan |
| C03 | Blocking-chain response | Identify the root blocker and waiters, then give a safe response order |
| C04 | Connection and transaction pressure | Connection limit, idle-in-transaction sessions, and connection-pool attribution |
| C05 | Vacuum and bloat investigation | Target table, autovacuum settings, dead-tuple evidence, and cautious conclusions |
| C06 | Index-set audit | Identify duplicate indexes by definition and combine workload evidence with add/drop advice |
| C07 | Optimizer estimates and statistics | Estimated versus actual rows, correlated columns, statistics freshness, and validation plan |
| C08 | Payment reconciliation | Exact counts, samples, and assumptions for six synthetic anomalies |
| C09 | Least privilege | Account violations, `PUBLIC CREATE`, and a reversible remediation draft |
| C10 | Policy-grounded incident decision | Support policy-specific claims with the named source, combine live evidence, and respect approval boundaries |

## Case boundaries

Each case defines a task, fixture state, reference-answer checks, optional authoritative-source requirements, and prohibited outcomes. It does not prescribe tool choice or a minimum call count. The runner preserves raw evidence and verifies after every attempt that the fixture database received no prohibited changes.

The catalog is limited to common production DBA work: health baselines, slow queries, blocking, connection and transaction pressure, maintenance, indexing, optimizer statistics, consistency checks, privileges, and incident decisions. Cases must not target Praxis implementation details such as a particular Skill, prompt, verifier, retry mechanism, or tool-call path. Deterministic behavior of those components belongs in tests; their effect on a general database task is reflected only in the Eval result.

Case definitions are versioned in the public Eval corpus and form a reproducible quality benchmark.

For prerequisites, commands, parameters, result classifications, and report artifacts, see the [Eval overview](evaluation.md).
