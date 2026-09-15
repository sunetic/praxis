# Praxis observability demo

This Compose project is a first-run experience environment. It is separate from
the default `docker-compose.yml` and builds the application as `praxis-demo:local`.
It must not be used as a production deployment template.

## Start with one command

Prerequisite: Docker with Compose v2.

```bash
docker compose -f deployments/demo/docker-compose.yml up -d --build
```

Compose uses fixed demo-only credentials, builds Praxis, starts the complete
stack, initializes the demo objects through the real Praxis HTTP API, and
verifies both integrations. All published ports bind to `127.0.0.1`; do not use
this configuration as a production deployment template.

The default MySQL connection from the host is:

| Field | Default |
| --- | --- |
| Address | `127.0.0.1:3308` |
| Database | `app` |
| Username | `app` |
| Password | `praxis-demo-app` |
| Root password | `praxis-demo-root` |

Inside the Compose network, Praxis connects to `mysql-demo:3306`. Prometheus is
available from the host at <http://127.0.0.1:9090> and from Praxis at
`http://prometheus-demo:9090`.

Open <http://127.0.0.1:8000>. The onboarding page still requires the user to
enter LLM provider, model, API URL, and API key information.

On a fresh volume the environment already contains:

- a `Demo MySQL` datasource with cluster key `mysql-prometheus-demo`;
- a cluster-bound `Demo Prometheus` Service whose connection test passes;
- the installed `Prometheus HTTP API` knowledge pack, linked to the Service;
- MySQL exporter metrics, two hours of Prometheus retention, and a workload that
  triggers `MySQLConnectionPressure` after approximately 20 seconds.

After onboarding, select `Demo MySQL` in **Chat** and try this request:

> Analyze the current MySQL connection pressure. Combine the database snapshot,
> the last two minutes of Prometheus history, and active alerts. State the time
> range and evidence source.

## Operations

```bash
# Show status
docker compose -f deployments/demo/docker-compose.yml ps

# Confirm automatic registration completed
docker compose -f deployments/demo/docker-compose.yml logs demo-init

# Stop containers while preserving demo data
docker compose -f deployments/demo/docker-compose.yml down

# Delete containers and demo volumes
docker compose -f deployments/demo/docker-compose.yml down --volumes
```

A successful initializer prints `"status": "ready"`. If an older or failed
demo volume does not contain the two integrations, remove the demo volumes with
the last command above and start the stack again. This deletes demo-only data.

All published ports bind to `127.0.0.1`. To avoid local port conflicts, override
`PRAXIS_DEMO_PORT`, `DEMO_MYSQL_PORT`, `DEMO_PROMETHEUS_PORT`, or
`DEMO_EXPORTER_PORT` before running the Compose command. The demo database
passwords can likewise be overridden with `DEMO_MYSQL_ROOT_PASSWORD`,
`DEMO_MYSQL_APP_PASSWORD`, and `DEMO_EXPORTER_PASSWORD`.
