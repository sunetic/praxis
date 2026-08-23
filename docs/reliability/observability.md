# Observability

Praxis observability answers three questions: what did the user see, what did the Agent actually do, and where did time or failure occur?

## User-visible execution evidence

Chat, Function, and Scheduler expose steps, tool results, status, duration, or run history. High-risk actions also create a pending-confirmation state. This evidence supports live decisions and failure recovery; it should not appear as ordinary user messages pretending to be conversation.

## End-to-end tracing

Praxis uses OpenTelemetry to collect spans for HTTP, model requests, selected database access, Chat tool calls, and Scheduler runs. By default, spans are stored in a local SQLite trace database. Query them through the API:

| Request | Purpose |
| --- | --- |
| `GET /api/v1/traces?minutes=60&limit=50` | Recent traces |
| `GET /api/v1/traces/slow?threshold_ms=1000` | Slow traces |
| `GET /api/v1/traces/{trace_id}` | Span tree for one trace |
| `POST /api/v1/traces/cleanup` | Remove expired spans |

Related environment settings include `TRACING_ENABLED`, `TRACING_DB_PATH`, `TRACING_SAMPLE_RATE`, and `TRACING_RETENTION_HOURS`. Before reducing the sample rate in a high-volume environment, make sure critical failures will still have enough samples.

## Troubleshooting order

1. Inspect the final UI state and error classification.
2. For model connection or rate-limit failures, inspect provider responses and retries.
3. If a task stops midway, inspect Chat, Function, or Scheduler events and tool results.
4. For slow requests or cross-component failures, locate the costly span in the trace.
5. For platform-object changes, also inspect confirmation state and audit history.

Trace data may contain SQL summaries, request attributes, and error details. Restrict access to the trace API and files in production, set a sensible retention period, and never publish the raw trace database.

## Observability and Eval

Observability explains one run; [Eval](evaluation.md) repeatedly measures whether versions and models regress on fixed tasks. Both are necessary: without runtime evidence, Eval failures are hard to diagnose; without stable Eval, one trace says little about overall quality.
