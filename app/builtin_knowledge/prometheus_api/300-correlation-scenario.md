# MySQL connection-pressure correlation scenario

This runbook matches the repository's `observability-demo` Docker Compose profile.

## Service and datasource mapping

- MySQL datasource from the host: `127.0.0.1:3308`, database `app`, user `app`,
  cluster key `mysql-prometheus-demo`.
- MySQL datasource from the Praxis container: `mysql-demo:3306` with the same
  credentials and cluster key.
- Prometheus Service from the host: `http://127.0.0.1:9090`.
- Prometheus Service from the Praxis container: `http://prometheus-demo:9090`.
- Service health check: `GET /-/ready`, response format `auto`, no authentication.
- Bind the Service to `cluster:mysql-prometheus-demo` and link this knowledge base.

## Evidence workflow

1. Confirm the Prometheus scrape target with `/api/v1/targets?state=active` and
   require `health=up` for the `mysql-demo` job.
2. Query current MySQL state using `SHOW GLOBAL STATUS` for
   `Threads_connected`, `Threads_running`, and `Aborted_connects`, plus
   `SHOW GLOBAL VARIABLES LIKE 'max_connections'`.
3. Use `/api/v1/query_range` for connection-utilization history. Set `start` and
   `end` from the user's incident window and use `step=5s` for this demo.
4. Use `/api/v1/alerts` to check the active `MySQLConnectionPressure` alert.
5. Correlate by timestamp and distinguish facts: current SQL snapshot, historical
   metric samples, and alert-rule state.

Expected demo behavior: the load container holds enough connections to exceed 50%
of `max_connections`; after roughly 30 seconds the alert becomes firing. The final
diagnosis should say that the database is reachable but has sustained connection
pressure, cite the peak/current utilization, and identify whether the alert fired.

If Prometheus has no samples, report the scrape-health gap. Do not convert missing
monitoring data into a claim that the database was healthy.
