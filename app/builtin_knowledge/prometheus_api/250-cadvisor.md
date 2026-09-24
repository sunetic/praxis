# cAdvisor container metrics and interpretation

Source of truth: [cAdvisor](https://github.com/google/cadvisor).

cAdvisor exposes resource usage for containers running on its host. A single
cAdvisor target can therefore return series for Praxis, MySQL, Prometheus, and
cAdvisor itself. Select the intended container by labels before interpreting a
resource metric.

Useful metrics include:

| Metric | Meaning |
| --- | --- |
| `container_last_seen` | Most recent observation of a container |
| `container_cpu_usage_seconds_total` | Cumulative container CPU time |
| `container_memory_working_set_bytes` | Current actively used container memory |
| `container_network_receive_bytes_total` | Cumulative received network bytes |
| `container_network_transmit_bytes_total` | Cumulative transmitted network bytes |

Docker Compose attaches the service label
`container_label_com_docker_compose_service`. First inspect current series if the
runtime uses different labels, then select the demo database with:

```promql
container_last_seen{
  job="cadvisor-demo",
  container_label_com_docker_compose_service="mysql-demo"
}
```

CPU usage in cores over five minutes:

```promql
sum(rate(container_cpu_usage_seconds_total{
  job="cadvisor-demo",
  container_label_com_docker_compose_service="mysql-demo"
}[5m]))
```

Memory working set in MiB:

```promql
container_memory_working_set_bytes{
  job="cadvisor-demo",
  container_label_com_docker_compose_service="mysql-demo"
} / 1024 / 1024
```

The CPU rate is measured in CPU-seconds per second: `1` means one fully used CPU
core. cAdvisor container metrics describe the container resource boundary; they
do not identify which SQL statement or MySQL thread consumed the resources.
