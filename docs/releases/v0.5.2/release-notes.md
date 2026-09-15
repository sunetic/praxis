# Praxis v0.5.2 Release Notes

Release date: September 15, 2026

Praxis v0.5.2 completes the first-run Docker Compose experience.

## Changed

- `docker compose run --build --rm demo-init` is now the single demo startup command
  documented in both the English and Chinese README files.
- Compose builds the checked-out Praxis source, starts the complete stack, waits for both registered connections to
  pass their health checks, and prints the Praxis, MySQL, Prometheus, and exporter
  addresses together with the demo database credentials.
- Fresh demo environments automatically register `Demo MySQL` and
  `Demo Prometheus` in the same Praxis database used by the UI. The
  initializer also confirms both objects through fresh list API calls before
  reporting readiness.
- The Prometheus knowledge pack remains available but is no longer installed or
  linked automatically. Users can install it from the Knowledge Packs page when
  they need it.
- The Service page now follows the selected Chinese or English locale throughout
  the list, empty states, dialogs, validation, and action feedback.
- CI now rejects new hard-coded Chinese or English frontend copy. Existing debt
  is tracked by exact source fingerprints in a shrink-only baseline, so it cannot
  grow silently. CI also self-tests the rule, builds the frontend, and exercises
  the Service page in both locales.

## Default demo connection information

| Field | Default |
| --- | --- |
| Praxis | `http://127.0.0.1:8000` |
| MySQL address | `127.0.0.1:3308` |
| Database | `app` |
| Username | `app` |
| Password | `praxis-demo-app` |
| Root password | `praxis-demo-root` |
| Prometheus | `http://127.0.0.1:9090` |
| MySQL Exporter | `http://127.0.0.1:9104/metrics` |

These credentials are restricted to the local demo. Do not use this Compose
configuration as a production deployment template.
