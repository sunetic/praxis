# Prometheus HTTP API operational reference

Source of truth: [Prometheus HTTP API](https://prometheus.io/docs/prometheus/latest/querying/api/).

Prometheus exposes its stable API below `/api/v1`. Successful API responses use
`{"status":"success","data":...}`. Errors use `status=error` with `errorType` and
`error`; therefore callers must inspect both the HTTP status and the response-level
`status` field.

## Readiness

`GET /-/ready` returns HTTP 200 when the server can serve queries. Its response is
plain text, so a Service used for Prometheus should use `response_format=auto`.

## Instant query

`GET /api/v1/query`

Query parameters:

- `query` (required): PromQL expression.
- `time`: RFC3339 timestamp or Unix timestamp. Omit for the current time.
- `timeout`: optional evaluation timeout such as `30s`.

Example Service call:

```json
{
  "method": "GET",
  "path": "/api/v1/query",
  "query_params": {
    "query": "mysql_global_status_threads_connected{job=\"mysql-demo\"}"
  }
}
```

The result type is usually `vector`; each result contains a `metric` label map and
`value: [unix_timestamp, string_value]`.

## Range query

`GET /api/v1/query_range`

Required query parameters:

- `query`: PromQL expression.
- `start`: inclusive RFC3339 or Unix timestamp.
- `end`: inclusive RFC3339 or Unix timestamp.
- `step`: query resolution, as a duration such as `15s` or seconds as a number.

The result type is normally `matrix`; each series contains
`values: [[unix_timestamp, string_value], ...]`. Use range queries for historical
claims such as sustained pressure, peaks, recovery time, or correlation with an
incident window. Do not infer a historical trend from an instant query.

## Active alerts

`GET /api/v1/alerts` returns the currently active alerts from alerting rules. Each
item includes labels, annotations, state, value, and `activeAt`.

`GET /api/v1/rules?type=alert` returns alerting rule evaluation state. Prometheus
does not provide a durable alert-event history through `/api/v1/alerts`; use
Alertmanager or a separate history store when the question concerns cleared alerts.

## Targets and metadata

- `GET /api/v1/targets?state=active` verifies scrape health and exposes the last
  scrape time and last error.
- `GET /api/v1/metadata?metric=<name>` returns metric type and help metadata.

Always check target health before interpreting an empty time series as evidence of
zero activity.
