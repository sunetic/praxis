# Praxis observability demo

This Compose project is a first-run experience environment. It is separate from
the default `docker-compose.yml` and builds the application as `praxis-demo:local`.
It must not be used as a production deployment template.

## Start with one command

Prerequisite: Docker with Compose v2.

```bash
./deployments/demo/start.sh
```

The script generates local credentials in an ignored `.env` file, builds Praxis,
starts the complete stack, initializes the demo objects through the real Praxis
HTTP API, and verifies both integrations.

Open <http://127.0.0.1:8000>. The onboarding page still requires the user to
enter LLM provider, model, API URL, and API key information.

On a fresh volume the environment already contains:

- a `Demo MySQL` datasource with cluster key `mysql-prometheus-demo`;
- a cluster-bound `Demo Prometheus` Service whose connection test passes;
- the bundled `Prometheus HTTP API` knowledge pack in `Available` status;
- MySQL exporter metrics, two hours of Prometheus retention, and a workload that
  triggers `MySQLConnectionPressure` after approximately 20 seconds.

After onboarding, install the Prometheus knowledge pack from **Knowledge**, link
it to `Demo Prometheus` if desired, and try this Chat request:

> Analyze the current MySQL connection pressure. Combine the database snapshot,
> the last two minutes of Prometheus history, and active alerts. State the time
> range and evidence source.

## Operations

```bash
# Show status
docker compose --env-file deployments/demo/.env \
  -f deployments/demo/docker-compose.yml ps

# Stop containers while preserving demo data
docker compose --env-file deployments/demo/.env \
  -f deployments/demo/docker-compose.yml down

# Delete containers and demo volumes
docker compose --env-file deployments/demo/.env \
  -f deployments/demo/docker-compose.yml down --volumes
```

All published ports bind to `127.0.0.1`. To avoid local port conflicts, override
`PRAXIS_DEMO_PORT`, `DEMO_MYSQL_PORT`, `DEMO_PROMETHEUS_PORT`, or
`DEMO_EXPORTER_PORT` when running `start.sh`.
