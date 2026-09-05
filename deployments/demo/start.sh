#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.yml"

generate_secret() {
  od -An -N24 -tx1 /dev/urandom | tr -d ' \n'
}

if [[ ! -f "${ENV_FILE}" ]]; then
  umask 077
  root_password="$(generate_secret)"
  app_password="$(generate_secret)"
  exporter_password="$(generate_secret)"
  {
    printf 'DEMO_MYSQL_ROOT_PASSWORD=%s\n' "${root_password}"
    printf 'DEMO_MYSQL_APP_PASSWORD=%s\n' "${app_password}"
    printf 'DEMO_EXPORTER_PASSWORD=%s\n' "${exporter_password}"
  } > "${ENV_FILE}"
  echo "Generated local demo credentials in ${ENV_FILE}"
fi

export PRAXIS_DEMO_PORT="${PRAXIS_DEMO_PORT:-8000}"
export DEMO_MYSQL_PORT="${DEMO_MYSQL_PORT:-3308}"
export DEMO_PROMETHEUS_PORT="${DEMO_PROMETHEUS_PORT:-9090}"
export DEMO_EXPORTER_PORT="${DEMO_EXPORTER_PORT:-9104}"

compose=(docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}")

"${compose[@]}" up -d --build \
  praxis-demo mysql-demo mysql-exporter-demo prometheus-demo mysql-load-demo
"${compose[@]}" run --rm demo-init

echo
echo "Praxis demo is ready: http://127.0.0.1:${PRAXIS_DEMO_PORT}"
echo "Enter your LLM settings in the onboarding page to start using Chat."
echo "Prometheus: http://127.0.0.1:${DEMO_PROMETHEUS_PORT}"
