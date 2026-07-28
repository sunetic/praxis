.PHONY: install dev test lint guard-semantic migrate rollback migrate-create clean seed testbed-init testbed-once testbed-start testbed-stop testbed-status testbed-case rules-health handbook-build handbook-serve docker-build docker-up docker-down

TESTBED_DATASOURCE_ID ?=
TESTBED_PREFIX ?= tb_
TESTBED_DATABASE ?=
TESTBED_TARGET_ROWS ?= 1000000
TESTBED_BATCH_SIZE ?= 5000
TESTBED_ITERATIONS ?= 50
TESTBED_INTERVAL_SECONDS ?= 1
TESTBED_PROBLEM_RATIO ?= 0.2
TESTBED_SLOW_THRESHOLD_MS ?= 500
TESTBED_DURATION_SECONDS ?= 0
TESTBED_SCENARIO_ENABLED ?= true
TESTBED_SCENARIO_CASE ?= all
TESTBED_SCENARIO_REFRESH_SECONDS ?= 300
TESTBED_CASE ?= all
TESTBED_SCHEDULER_PROFILE ?=
TESTBED_TABLE_PROFILE ?=
TESTBED_STATS_PROFILE ?=
TESTBED_WORKLOAD_PROFILE ?=
TESTBED_OPS_PROFILE ?=
TESTBED_ALLOW_PLAN_CACHE_SIDE_EFFECT ?= auto
TESTBED_API_BASE ?=
TESTBED_PASSWORD ?=
TESTBED_PID_FILE ?= tmp/testbed.pid
TESTBED_LOG_FILE ?= tmp/testbed.log

UNAME_S := $(shell uname -s)
ifeq ($(UNAME_S),Linux)
UV_ENV := UV_PROJECT_ENVIRONMENT=.venv-linux
else
UV_ENV :=
endif

install:
	$(UV_ENV) uv sync

dev:
	$(UV_ENV) uv sync
	$(UV_ENV) uv run python -m uvicorn app.main:app --reload --reload-dir app --port 8000

test:
	uv run pytest

lint:
	uv run ruff check app/
	uv run python tools/semantic_guard_scan.py --root . --paths app tests

guard-semantic:
	uv run python tools/semantic_guard_scan.py --root . --paths app tests

migrate:
	uv run alembic upgrade head

seed:
	uv run python tools/seed.py

rollback:
	uv run alembic downgrade -1

migrate-create:
	uv run alembic revision --autogenerate -m "$(message)"

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	rm -f praxis.db

testbed-init:
	@test -n "$(TESTBED_DATASOURCE_ID)" || (echo "TESTBED_DATASOURCE_ID is required" && exit 1)
	uv run python testbed/runner.py init \
		--datasource-id "$(TESTBED_DATASOURCE_ID)" \
		$(if $(TESTBED_API_BASE),--api-base "$(TESTBED_API_BASE)",) \
		$(if $(TESTBED_PASSWORD),--password "$(TESTBED_PASSWORD)",) \
		$(if $(TESTBED_DATABASE),--database-override "$(TESTBED_DATABASE)",) \
		--prefix "$(TESTBED_PREFIX)" \
		--target-rows "$(TESTBED_TARGET_ROWS)" \
		--batch-size "$(TESTBED_BATCH_SIZE)"

testbed-once:
	@test -n "$(TESTBED_DATASOURCE_ID)" || (echo "TESTBED_DATASOURCE_ID is required" && exit 1)
	uv run python testbed/runner.py once \
		--datasource-id "$(TESTBED_DATASOURCE_ID)" \
		$(if $(TESTBED_API_BASE),--api-base "$(TESTBED_API_BASE)",) \
		$(if $(TESTBED_PASSWORD),--password "$(TESTBED_PASSWORD)",) \
		$(if $(TESTBED_DATABASE),--database-override "$(TESTBED_DATABASE)",) \
		--prefix "$(TESTBED_PREFIX)" \
		--iterations "$(TESTBED_ITERATIONS)" \
		--problem-ratio "$(TESTBED_PROBLEM_RATIO)" \
		--slow-threshold-ms "$(TESTBED_SLOW_THRESHOLD_MS)"

testbed-start:
	@test -n "$(TESTBED_DATASOURCE_ID)" || (echo "TESTBED_DATASOURCE_ID is required" && exit 1)
	@mkdir -p tmp
	@if [ -f "$(TESTBED_PID_FILE)" ] && kill -0 "$$(cat "$(TESTBED_PID_FILE)")" 2>/dev/null; then \
		echo "testbed is already running with pid $$(cat "$(TESTBED_PID_FILE)")"; \
		exit 1; \
	fi
	nohup env PYTHONUNBUFFERED=1 uv run python testbed/runner.py run \
		--datasource-id "$(TESTBED_DATASOURCE_ID)" \
		$(if $(TESTBED_API_BASE),--api-base "$(TESTBED_API_BASE)",) \
		$(if $(TESTBED_PASSWORD),--password "$(TESTBED_PASSWORD)",) \
		$(if $(TESTBED_DATABASE),--database-override "$(TESTBED_DATABASE)",) \
		--prefix "$(TESTBED_PREFIX)" \
		--interval-seconds "$(TESTBED_INTERVAL_SECONDS)" \
		--problem-ratio "$(TESTBED_PROBLEM_RATIO)" \
		--slow-threshold-ms "$(TESTBED_SLOW_THRESHOLD_MS)" \
		--duration-seconds "$(TESTBED_DURATION_SECONDS)" \
		--scenario-enabled "$(TESTBED_SCENARIO_ENABLED)" \
		--scenario-case "$(TESTBED_SCENARIO_CASE)" \
		--scenario-refresh-seconds "$(TESTBED_SCENARIO_REFRESH_SECONDS)" \
		> "$(TESTBED_LOG_FILE)" 2>&1 & echo $$! > "$(TESTBED_PID_FILE)"
	@echo "testbed started, pid=$$(cat "$(TESTBED_PID_FILE)") log=$(TESTBED_LOG_FILE)"

testbed-stop:
	@if [ ! -f "$(TESTBED_PID_FILE)" ]; then \
		echo "testbed is not running"; \
		exit 0; \
	fi
	@if kill -0 "$$(cat "$(TESTBED_PID_FILE)")" 2>/dev/null; then \
		kill "$$(cat "$(TESTBED_PID_FILE)")" && echo "testbed stopped"; \
	else \
		echo "stale pid file, cleaning"; \
	fi
	@rm -f "$(TESTBED_PID_FILE)"

testbed-status:
	@if [ -z "$(TESTBED_DATASOURCE_ID)" ]; then \
		echo "---- running testbeds ----"; \
		uv run python -c "import subprocess; out = subprocess.check_output(['ps', '-ax', '-o', 'pid=,command='], text=True); lines = [line.strip() for line in out.splitlines() if 'testbed/runner.py run' in line and 'uv run python -c' not in line and '-- ps -ax -o pid=,command=' not in line]; print('\\n'.join(lines) if lines else 'runner_status=stopped')"; \
	else \
		if [ -f "$(TESTBED_PID_FILE)" ] && kill -0 "$$(cat "$(TESTBED_PID_FILE)")" 2>/dev/null; then \
			echo "runner_status=running pid=$$(cat "$(TESTBED_PID_FILE)")"; \
		else \
			echo "runner_status=stopped"; \
		fi; \
		echo "---- db status ----"; \
		uv run python testbed/runner.py status \
			--datasource-id "$(TESTBED_DATASOURCE_ID)" \
			$(if $(TESTBED_API_BASE),--api-base "$(TESTBED_API_BASE)",) \
			$(if $(TESTBED_PASSWORD),--password "$(TESTBED_PASSWORD)",) \
			$(if $(TESTBED_DATABASE),--database-override "$(TESTBED_DATABASE)",) \
			--prefix "$(TESTBED_PREFIX)"; \
	fi

testbed-case:
	@test -n "$(TESTBED_DATASOURCE_ID)" || (echo "TESTBED_DATASOURCE_ID is required" && exit 1)
	uv run python testbed/runner.py case \
		--datasource-id "$(TESTBED_DATASOURCE_ID)" \
		$(if $(TESTBED_API_BASE),--api-base "$(TESTBED_API_BASE)",) \
		$(if $(TESTBED_PASSWORD),--password "$(TESTBED_PASSWORD)",) \
		$(if $(TESTBED_DATABASE),--database-override "$(TESTBED_DATABASE)",) \
		--prefix "$(TESTBED_PREFIX)" \
		--target-rows "$(TESTBED_TARGET_ROWS)" \
		--batch-size "$(TESTBED_BATCH_SIZE)" \
		--iterations "$(TESTBED_ITERATIONS)" \
		--case-name "$(TESTBED_CASE)" \
		--allow-plan-cache-side-effect "$(TESTBED_ALLOW_PLAN_CACHE_SIDE_EFFECT)" \
		$(if $(TESTBED_SCHEDULER_PROFILE),--scheduler-profile "$(TESTBED_SCHEDULER_PROFILE)",) \
		$(if $(TESTBED_TABLE_PROFILE),--table-profile "$(TESTBED_TABLE_PROFILE)",) \
		$(if $(TESTBED_STATS_PROFILE),--stats-profile "$(TESTBED_STATS_PROFILE)",) \
		$(if $(TESTBED_WORKLOAD_PROFILE),--workload-profile "$(TESTBED_WORKLOAD_PROFILE)",) \
		$(if $(TESTBED_OPS_PROFILE),--ops-profile "$(TESTBED_OPS_PROFILE)",)

rules-health:
	uv run python tools/rules_hygiene_check.py

VERSION := $(shell grep '^version' pyproject.toml | head -1 | sed 's/.*= *"\(.*\)"/\1/')

docker-build:
	docker build --no-cache \
		-t praxis:$(VERSION) \
		-t praxis:latest \
		.

docker-up:
	docker compose up

docker-down:
	docker compose down

# MkDocs Material → site_handbook/ ; FastAPI mounts at http://localhost:8000/handbook/
handbook-build:
	uv run mkdocs build -f mkdocs.yml

handbook-serve:
	uv run mkdocs serve -f mkdocs.yml -a 127.0.0.1:8001 --livereload --no-strict
