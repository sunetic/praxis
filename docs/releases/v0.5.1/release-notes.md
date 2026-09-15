# Praxis v0.5.1 Release Notes

Release date: September 15, 2026

Praxis v0.5.1 repairs the first-run Docker Compose experience introduced with
the observability demo.

## Fixed

- The root `docker-compose.yml` now starts the complete local demo stack:
  Praxis, MySQL, MySQL Exporter, Prometheus, the connection-pressure workload,
  and the automatic initializer.
- The initializer creates the visible `Demo MySQL` datasource and
  `Demo Prometheus` Service before installing optional knowledge enrichment.
- Transient initializer failures are retried, and restarting the stack reuses
  existing objects instead of creating duplicates.
- Successful initialization prints `"status": "ready"` together with the
  host-side MySQL connection details.
- The English and Chinese quick-start guides now list the demo MySQL username,
  passwords, ports, Prometheus URL, readiness check, and reset procedure.
- CI now starts the real Compose stack and asserts that both integrations were
  registered and the Prometheus knowledge base was linked.

## Default demo credentials

| Field | Default |
| --- | --- |
| MySQL address | `127.0.0.1:3308` |
| Database | `app` |
| Username | `app` |
| Password | `praxis-demo-app` |
| Root password | `praxis-demo-root` |
| Prometheus | `http://127.0.0.1:9090` |

These credentials are restricted to the local demo. Do not use this Compose
configuration as a production deployment template.

## Validation

- Complete non-LLM backend suite: 580 passed, 4 skipped, 7 deselected.
- Demo initializer suite: 10 passed.
- Ruff lint, format, repository hygiene, strict documentation build, and YAML
  parsing passed.
- A clean Praxis database accepted the real initializer HTTP requests, created
  both integrations, installed and linked the bundled Prometheus pack, and
  produced the same object IDs on a second idempotency run.
