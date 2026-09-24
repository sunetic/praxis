# MySQL container-resource correlation scenario

This runbook matches the repository's Docker Compose demo.

## Service and datasource mapping

- MySQL datasource from the host: `127.0.0.1:3308`, database `app`, user `app`,
  cluster key `mysql-prometheus-demo`.
- MySQL datasource from the Praxis container: `mysql-demo:3306` with the same
  credentials and cluster key.
- Prometheus Service from the Praxis container: `http://prometheus-demo:9090`.
- Prometheus scrapes cAdvisor as `job="cadvisor-demo"`.
- The MySQL container is selected with the Compose service label `mysql-demo`.

## Evidence workflow

1. Confirm the cAdvisor target with `/api/v1/targets?state=active` and require an
   up target for `job="cadvisor-demo"`.
2. Use `container_last_seen` to resolve the current MySQL container series and
   its available labels instead of assuming a generated container name.
3. Use `/api/v1/query_range` for MySQL container CPU or memory history. Set
   `start` and `end` from the user's requested window.
4. When database context is relevant, query the selected MySQL datasource for a
   current SQL snapshot and keep it distinct from historical container metrics.
5. Correlate facts by timestamp, container identity, and units. State which
   claims came from SQL and which came from Prometheus.

Prometheus retention is two hours in this demo. An empty series outside that
window is a coverage gap, not evidence of zero usage.
