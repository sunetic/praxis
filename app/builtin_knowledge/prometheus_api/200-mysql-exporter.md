# MySQL exporter metrics and interpretation

Source of truth: [prometheus/mysqld_exporter](https://github.com/prometheus/mysqld_exporter).

The MySQL exporter reads server status and performance information and exposes it
as Prometheus metrics. The exporter user should have `PROCESS`,
`REPLICATION CLIENT`, and `SELECT` grants and a small connection limit.

Useful metrics in the bundled demonstration:

| Metric | Meaning | Correlation use |
| --- | --- | --- |
| `mysql_up` | Exporter can query MySQL | Reject conclusions from unavailable scrape data |
| `mysql_global_status_threads_connected` | Currently open client connections | Compare with SQL `Threads_connected` and query historical pressure |
| `mysql_global_variables_max_connections` | Configured server connection limit | Calculate utilization rather than using an absolute count |
| `mysql_global_status_threads_running` | Threads actively running | Separate many idle sessions from active concurrency |
| `mysql_global_status_aborted_connects` | Failed connection attempts since startup | Use `increase(...[window])` for incident-window failures |
| `mysql_global_status_questions` | Statements executed since startup | Use `rate(...[window])` for workload intensity |

Recommended PromQL:

```promql
100 * mysql_global_status_threads_connected{job="mysql-demo"}
  / mysql_global_variables_max_connections{job="mysql-demo"}
```

```promql
max_over_time(mysql_global_status_threads_connected{job="mysql-demo"}[15m])
```

```promql
increase(mysql_global_status_aborted_connects{job="mysql-demo"}[15m])
```

Counters such as `questions` and `aborted_connects` require `rate` or `increase`
over a time window. Gauges such as `threads_connected` can be queried directly.
Align label selectors and timestamps before comparing Prometheus with SQL output.
