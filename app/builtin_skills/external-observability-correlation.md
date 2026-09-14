---
name: external-observability-correlation
version: 1.0.0
description: Correlate database evidence with external monitoring metrics, alerts, and HTTP APIs
database: general
always_apply: false
source: built_in
---
# External Observability Correlation

## Goal

Produce a time-aligned diagnosis that combines live database evidence with external
monitoring or alerting evidence exposed by a registered HTTP Service. This workflow
is provider-neutral: Prometheus is one implementation; Alertmanager, Grafana,
and customer-built APIs follow the same discovery and evidence rules.

## Required workflow

1. Read **Available Services** in the capability context. Identify the relevant
   `service_id`, `service_type`, and `linked_kb_ids`. If several Services are bound,
   choose by provider purpose; never rely on implicit auto-binding.
2. Read the bound provider contract on `call_praxis_service` first. If it already
   names the required endpoint and metric, call it directly. Otherwise perform one
   focused `knowledge_search` with the linked knowledge-base IDs before calling the
   API. Do not guess identifiers, invent PromQL, or enumerate a provider's complete
   metric/label catalog as a shortcut for focused documentation lookup.
3. Check service/readiness or scrape health when the provider documentation defines
   such a preflight. Missing or unhealthy monitoring data is an evidence gap, not a
   healthy result.
4. Collect database facts with `execute_sql` and external facts with
   `call_praxis_service`. Independent current-state calls may run together; a range
   query must use the user's incident window or an explicitly stated fallback
   window.
5. Correlate evidence by timestamp, resource labels, and units. Distinguish gauges,
   counters, rates, aggregates, and alert states. Do not compare unmatched scopes.
   Preserve the timestamp format returned by the provider. A Unix epoch is UTC, but
   do not mentally convert it to a calendar timestamp; state the raw interval and
   relative duration unless a tool result supplies a verified conversion. Keep alert
   `activeAt` separate from the metric query window.
6. State provenance in the conclusion: which claims came from SQL, which came from
   the external Service, and which are inferred from their alignment.
7. Stop discovery when the evidence required by the user's question is present.
   Do not add adjacent metadata calls that cannot change the conclusion.

## Prometheus example

- Instant values: `/api/v1/query`.
- Historical samples: `/api/v1/query_range` with `query`, `start`, `end`, and
  `step` in `query_params`.
- Current alerts: `/api/v1/alerts`.
- Scrape health: `/api/v1/targets?state=active`.

These paths are examples only. Confirm them in the linked knowledge base and use the
provider's own documentation when the selected Service is not Prometheus.

## Completion requirements

- Include at least one actual database result and one actual external-Service result
  when both capabilities are available.
- Quote the analyzed time window and identify timezone assumptions.
- Report empty series, partial coverage, query errors, and absent alert history
  explicitly.
- Never claim historical persistence from a current snapshot.
