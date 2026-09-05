#!/usr/bin/env bash
set -euo pipefail

until mysqladmin ping -h mysql-demo -uapp --silent; do
  sleep 2
done

while true; do
  pids=()
  for _ in $(seq 1 14); do
    mysql -h mysql-demo -uapp app --connect-timeout=5 -e "SELECT SLEEP(300)" &
    pids+=("$!")
  done
  for pid in "${pids[@]}"; do
    wait "${pid}" || true
  done
done
